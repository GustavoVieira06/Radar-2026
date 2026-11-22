# -*- coding: utf-8 -*-
"""
Construção do índice invertido em disco (SPIMI - Single-Pass In-Memory Indexing).

1. INVERSÃO EM BLOCOS: os documentos (ou trechos) são lidos em sequência e o
   índice parcial vai sendo montado na memória (termo -> postings já
   comprimidos). A cada `bloco_docs` documentos o bloco é ordenado por termo e
   gravado em disco (blocos/bloco-NNNN.bin), e a memória é liberada.
2. FUSÃO: os blocos são lidos ao mesmo tempo e intercalados por termo (k-way
   merge com heap). As postings de um termo vindas de blocos diferentes são
   concatenadas (só o 1º d-gap de cada bloco precisa ser recalculado).
3. BUCKETS: o vocabulário final é dividido em `buckets` grupos pelo hash
   (CRC32) do termo. Cada bucket tem 3 arquivos:
       vocabulario/bucket-NNNN.tsv  termo, df, cf, deslocamento e tamanho nas
                                    postings, deslocamento e tamanho nas posições
       postings/bucket-NNNN.bin     listas de postings (d-gap + VByte)
       posicoes/bucket-NNNN.bin     posições de cada ocorrência (Etapa 3)
   Na busca, só o vocabulário do bucket do termo precisa ser carregado.

ÍNDICE POSICIONAL (Etapa 3): para cada posting (unidade, tf) guardam-se as tf
posições em que o termo aparece no documento (posição = índice do token no
texto original, contando as stopwords, para que "plano de governo" exija
'governo' 2 posições depois de 'plano'). As posições ficam num arquivo à parte,
na mesma ordem das postings, também com d-gaps + VByte (a 1ª posição de cada
posting é absoluta). Assim, a busca comum (BM25) continua lendo só as postings,
e as posições só são lidas nas buscas por frase.

Granularidade: 'documento' (1 unidade = 1 documento) ou 'trecho' (cada
documento é cortado em trechos de `tam_trecho` termos; 1 unidade = 1 trecho).
"""
from __future__ import unicode_literals, division

import collections
import heapq
import io
import json
import mmap
import os
import shutil
import time
import zlib

from . import corpus
from .codificacao import vbyte, vbyte_bytes, ler_vbyte

try:
    import psutil
    _PROC = psutil.Process()
except Exception:  # psutil não instalado
    _PROC = None


def memoria_mb():
    if _PROC is None:
        return None
    return round(_PROC.memory_info().rss / 1048576.0, 1)


def _tamanho_pasta(p):
    total = 0
    for atual, _, arqs in os.walk(p):
        for a in arqs:
            total += os.path.getsize(os.path.join(atual, a))
    return total


# ---------------------------------------------------------------------------
# 1. inversão em blocos
# ---------------------------------------------------------------------------
def _gravar_bloco(parcial, caminho):
    """parcial: termo -> [ultima_unidade, bytearray, n_postings, cf, primeira, posicoes]"""
    with io.open(caminho, 'wb') as fp:
        buf = bytearray()
        for termo in sorted(parcial):
            ult, dados, n, cf, prim, pos = parcial[termo]
            tb = termo.encode('utf-8')
            vbyte(len(tb), buf)
            buf += tb
            vbyte(n, buf)
            vbyte(cf, buf)
            vbyte(prim, buf)
            vbyte(ult, buf)
            vbyte(len(dados), buf)
            buf += dados
            vbyte(len(pos), buf)
            buf += pos
            if len(buf) > 1 << 22:
                fp.write(buf)
                buf = bytearray()
        fp.write(buf)


def _ler_bloco(caminho, ordem):
    # mmap: o bloco não é carregado inteiro na memória (lido sob demanda)
    with io.open(caminho, 'rb') as fp, mmap.mmap(fp.fileno(), 0, access=mmap.ACCESS_READ) as buf:
        pos, fim = 0, len(buf)
        while pos < fim:
            lt, pos = ler_vbyte(buf, pos)
            termo = buf[pos:pos + lt].decode('utf-8')
            pos += lt
            n, pos = ler_vbyte(buf, pos)
            cf, pos = ler_vbyte(buf, pos)
            prim, pos = ler_vbyte(buf, pos)
            ult, pos = ler_vbyte(buf, pos)
            ld, pos = ler_vbyte(buf, pos)
            dados = buf[pos:pos + ld]
            pos += ld
            lp, pos = ler_vbyte(buf, pos)
            yield termo, ordem, n, cf, prim, ult, dados, buf[pos:pos + lp]
            pos += lp


def inverter(normalizador, dir_preparo, dir_indice, granularidade='documento', tam_trecho=250,
             bloco_docs=10000, log=print, limite_docs=None, posicional=True):
    """Lê a coleção tokenizada e grava os blocos. Devolve estatísticas."""
    dir_blocos = os.path.join(dir_indice, 'blocos')
    if os.path.isdir(dir_blocos):
        shutil.rmtree(dir_blocos)
    os.makedirs(dir_blocos)
    t0 = time.time()
    t_norm = t_inv = t_bloco = 0.0
    parcial = {}
    blocos = []
    docs_no_bloco = 0
    unidade = 0
    n_docs = 0
    termos_total = 0
    vocab = set()
    heaps = []
    mem_pico = memoria_mb() or 0
    fun = open(os.path.join(dir_indice, 'unidades.tsv'), 'w')
    fun.write('unidade\tdocid\tordem\tn_termos\n')

    for tokens in corpus.tokens_por_documento(dir_preparo):
        if limite_docs and n_docs >= limite_docs:
            break
        ta = time.time()
        termos, posicoes = normalizador.termos_com_posicoes(tokens)
        tb = time.time()
        t_norm += tb - ta
        if granularidade == 'trecho':
            cortes = range(0, len(termos), tam_trecho)
            partes = [(termos[i:i + tam_trecho], posicoes[i:i + tam_trecho]) for i in cortes] or [([], [])]
        else:
            partes = [(termos, posicoes)]
        for ordem, (parte, pos_parte) in enumerate(partes):
            fun.write('%d\t%d\t%d\t%d\n' % (unidade, n_docs, ordem, len(parte)))
            ocorr = collections.defaultdict(list)       # termo -> posições nesta unidade
            for termo, p in zip(parte, pos_parte):
                ocorr[termo].append(p)
            for termo, lista in ocorr.items():
                tf = len(lista)
                e = parcial.get(termo)
                if e is None:
                    d = bytearray()
                    vbyte(unidade, d)      # 1º gap do bloco = unidade absoluta
                    vbyte(tf, d)
                    e = parcial[termo] = [unidade, d, 1, tf, unidade, bytearray()]
                else:
                    d = e[1]
                    vbyte(unidade - e[0], d)
                    vbyte(tf, d)
                    e[0] = unidade
                    e[2] += 1
                    e[3] += tf
                if posicional:
                    pb = e[5]
                    ant = 0
                    for p in lista:               # d-gaps das posições (1ª absoluta)
                        vbyte(p - ant, pb)
                        ant = p
            vocab.update(parte)
            termos_total += len(parte)
            unidade += 1
        n_docs += 1
        docs_no_bloco += 1
        t_inv += time.time() - tb
        if n_docs % 1000 == 0:
            heaps.append((termos_total, len(vocab)))
        if docs_no_bloco >= bloco_docs:
            mem_pico = max(mem_pico, memoria_mb() or 0)
            tc = time.time()
            caminho = os.path.join(dir_blocos, 'bloco-%04d.bin' % len(blocos))
            _gravar_bloco(parcial, caminho)
            blocos.append(caminho)
            parcial = {}
            docs_no_bloco = 0
            t_bloco += time.time() - tc
            log('    bloco %d gravado (%d docs, %.0f s)' % (len(blocos), n_docs, time.time() - t0))
    if parcial:
        mem_pico = max(mem_pico, memoria_mb() or 0)
        tc = time.time()
        caminho = os.path.join(dir_blocos, 'bloco-%04d.bin' % len(blocos))
        _gravar_bloco(parcial, caminho)
        blocos.append(caminho)
        parcial = {}
        t_bloco += time.time() - tc
    fun.close()
    heaps.append((termos_total, len(vocab)))
    return {
        'documentos': n_docs,
        'unidades': unidade,
        'termos_indexados': termos_total,
        'blocos': len(blocos),
        'tamanho_blocos_bytes': _tamanho_pasta(dir_blocos),
        'tempo_normalizacao_s': round(t_norm, 2),
        'tempo_inversao_s': round(t_inv, 2),
        'tempo_escrita_blocos_s': round(t_bloco, 2),
        'tempo_fase1_s': round(time.time() - t0, 2),
        'memoria_pico_mb': mem_pico or None,
        'heaps_termos_vocabulario': heaps,
    }


# ---------------------------------------------------------------------------
# 2-3. fusão dos blocos + divisão em buckets
# ---------------------------------------------------------------------------
def bucket_de(termo, n_buckets):
    return zlib.crc32(termo.encode('utf-8')) % n_buckets


def fundir(dir_indice, n_buckets=64, dir_blocos=None, log=print):
    """k-way merge dos blocos -> vocabulario/ + postings/ (em n_buckets)."""
    t0 = time.time()
    dir_blocos = dir_blocos or os.path.join(dir_indice, 'blocos')
    blocos = sorted(os.path.join(dir_blocos, b) for b in os.listdir(dir_blocos))
    for sub in ('vocabulario', 'postings', 'posicoes'):
        p = os.path.join(dir_indice, sub)
        if os.path.isdir(p):
            shutil.rmtree(p)
        os.makedirs(p)
    arq_post = [io.open(os.path.join(dir_indice, 'postings', 'bucket-%04d.bin' % b), 'wb')
                for b in range(n_buckets)]
    arq_pos = [io.open(os.path.join(dir_indice, 'posicoes', 'bucket-%04d.bin' % b), 'wb')
               for b in range(n_buckets)]
    desloc = [0] * n_buckets
    desloc_pos = [0] * n_buckets
    vocab = [[] for _ in range(n_buckets)]
    leitores = [_ler_bloco(c, i) for i, c in enumerate(blocos)]
    n_termos = n_postings = 0
    mem_pico = memoria_mb() or 0

    def emitir(termo, partes):
        df = cf = 0
        dados = bytearray()
        posic = bytearray()
        ult = None
        for (_, _, n, c, prim, u, payload, pos_payload) in partes:
            posic += pos_payload                     # posições: só concatenar
            df += n
            cf += c
            if ult is None:
                dados += payload
            else:
                _, p = ler_vbyte(payload, 0)       # descarta o 1º gap (absoluto)
                dados += vbyte_bytes(prim - ult)
                dados += payload[p:]
            ult = u
        b = bucket_de(termo, n_buckets)
        arq_post[b].write(dados)
        arq_pos[b].write(posic)
        vocab[b].append('%s\t%d\t%d\t%d\t%d\t%d\t%d' % (termo, df, cf, desloc[b], len(dados),
                                                     desloc_pos[b], len(posic)))
        desloc[b] += len(dados)
        desloc_pos[b] += len(posic)
        return df

    atual, partes = None, []
    for item in heapq.merge(*leitores):
        if item[0] != atual:
            if partes:
                n_postings += emitir(atual, partes)
                n_termos += 1
                if n_termos % 200000 == 0:
                    mem_pico = max(mem_pico, memoria_mb() or 0)
            atual, partes = item[0], []
        partes.append(item)
    if partes:
        n_postings += emitir(atual, partes)
        n_termos += 1
    for f in arq_post + arq_pos:
        f.close()
    for b in range(n_buckets):
        with io.open(os.path.join(dir_indice, 'vocabulario', 'bucket-%04d.tsv' % b), 'w',
                     encoding='utf-8') as fp:
            fp.write('\n'.join(vocab[b]) + ('\n' if vocab[b] else ''))
    mem_pico = max(mem_pico, memoria_mb() or 0)
    tam_buckets = [os.path.getsize(os.path.join(dir_indice, 'postings', 'bucket-%04d.bin' % b))
                   for b in range(n_buckets)]
    return {
        'buckets': n_buckets,
        'vocabulario': n_termos,
        'postings': n_postings,
        'tempo_fusao_s': round(time.time() - t0, 2),
        'memoria_pico_fusao_mb': mem_pico or None,
        'tamanho_vocabulario_bytes': _tamanho_pasta(os.path.join(dir_indice, 'vocabulario')),
        'tamanho_postings_bytes': _tamanho_pasta(os.path.join(dir_indice, 'postings')),
        'tamanho_posicoes_bytes': _tamanho_pasta(os.path.join(dir_indice, 'posicoes')),
        'tamanho_unidades_bytes': os.path.getsize(os.path.join(dir_indice, 'unidades.tsv')),
        'postings_sem_compressao_bytes': n_postings * 8,   # 2 inteiros de 32 bits por posting
        'bucket_postings_min_bytes': min(tam_buckets),
        'bucket_postings_max_bytes': max(tam_buckets),
        'bucket_postings_medio_bytes': int(sum(tam_buckets) / n_buckets),
    }


def construir(normalizador, dir_preparo, dir_indice, granularidade='documento', tam_trecho=250,
              bloco_docs=10000, n_buckets=64, manter_blocos=False, log=print, nome=None,
              posicional=True):
    if not os.path.isdir(dir_indice):
        os.makedirs(dir_indice)
    t0 = time.time()
    log('  fase 1: inversao em blocos de %d documentos' % bloco_docs)
    est = inverter(normalizador, dir_preparo, dir_indice, granularidade, tam_trecho, bloco_docs, log,
                   posicional=posicional)
    log('  fase 2: fusao de %d blocos em %d buckets' % (est['blocos'], n_buckets))
    est.update(fundir(dir_indice, n_buckets, log=log))
    if not manter_blocos:
        shutil.rmtree(os.path.join(dir_indice, 'blocos'), ignore_errors=True)
    est['tempo_indexacao_total_s'] = round(time.time() - t0, 2)
    est['tamanho_indice_sem_posicoes_bytes'] = (est['tamanho_vocabulario_bytes']
                                                + est['tamanho_postings_bytes']
                                                + est['tamanho_unidades_bytes'])
    # como na Etapa 2 (comparável com os experimentos): vocabulário + postings + unidades
    est['tamanho_indice_bytes'] = est['tamanho_indice_sem_posicoes_bytes']
    est['tamanho_indice_com_posicoes_bytes'] = est['tamanho_indice_sem_posicoes_bytes'] + est['tamanho_posicoes_bytes']
    est['posicoes_por_ocorrencia_bytes'] = round(est['tamanho_posicoes_bytes']
                                                 / max(est['termos_indexados'], 1), 2)
    est['razao_compressao'] = round(est['postings_sem_compressao_bytes']
                                    / max(est['tamanho_postings_bytes'], 1), 2)
    est['bytes_por_posting'] = round(est['tamanho_postings_bytes'] / max(est['postings'], 1), 2)
    est['postings_por_termo'] = round(est['postings'] / max(est['vocabulario'], 1), 1)
    config = {
        'nome': nome or os.path.basename(dir_indice.rstrip('/\\')),
        'granularidade': granularidade,
        'tam_trecho': tam_trecho if granularidade == 'trecho' else None,
        'stopwords': normalizador.stopwords,
        'stemming': normalizador.stemming,
        'bloco_docs': bloco_docs,
        'buckets': n_buckets,
        'posicional': posicional,
        'dir_preparo': os.path.abspath(dir_preparo),
    }
    with io.open(os.path.join(dir_indice, 'config.json'), 'w', encoding='utf-8') as fp:
        json.dump({'config': config, 'estatisticas': est}, fp, ensure_ascii=False, indent=1)
    return config, est
