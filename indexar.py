#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Etapas 2 e 3 - Representação/Indexação e busca (índice invertido em arquivos, sem SGBD).
A interface gráfica fica em buscador.py.

Uso (dentro da pasta do projeto, com o venv ativado):
    python indexar.py preparar                  # limpeza + análise léxica (1 vez)
    python indexar.py construir --nome padrao   # constrói um índice (opções abaixo)
    python indexar.py experimentos              # tudo: prepara, constrói as variações
                                                #   e grava dados/indice/resultados/
    python indexar.py buscar "reforma tributaria" [--ano 2026] [--modo e] [--modelo tfidf]
    python indexar.py info [--indice padrao]

Opções do construir:
    --granularidade documento|trecho   --trecho 250   (tamanho do trecho em termos)
    --sem-stopwords   --sem-stemming   --bloco 10000  (docs por bloco SPIMI)
    --buckets 64      --manter-blocos
"""
from __future__ import print_function, unicode_literals, division

import argparse
import csv
import io
import json
import math
import os
import shutil
import subprocess
import sys
import time

RAIZ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, RAIZ)
for _f in (sys.stdout, sys.stderr):   # console do Windows: não quebra com acentos/aspas
    try:
        _f.reconfigure(errors='replace')
    except Exception:
        pass

from riw_indice import corpus, preproc, construtor  # noqa: E402

COLECAO = os.path.join(RAIZ, 'dados', 'coleta', 'promessas')
BASE = os.path.join(RAIZ, 'dados', 'indice')

CONSULTAS = [
    'reforma tributária', 'segurança pública', 'saúde pública', 'educação infantil',
    'plano de governo', 'salário mínimo', 'transporte público', 'meio ambiente',
    'combate à corrupção', 'geração de emprego', 'programa bolsa família',
    'privatização da petrobras', 'moradia popular', 'agronegócio e exportação',
    'desmatamento na amazônia', 'propostas para a educação', 'imposto de renda',
    'sistema único de saúde', 'violência contra a mulher', 'reajuste dos servidores',
]


def dir_preparo(args):
    return os.path.join(args.base, 'colecao')


def dir_idx(args, nome):
    return os.path.join(args.base, 'indices', nome)


def log(msg):
    print(msg)
    sys.stdout.flush()


# ---------------------------------------------------------------------------
def cmd_preparar(args):
    log('Preparando a colecao (limpeza + analise lexica) a partir de %s' % (args.csv or args.colecao))
    info = corpus.preparar(args.colecao, dir_preparo(args), getattr(args, 'limite_lotes', None), log,
                           csv_unificado=args.csv)
    if info.get('documentos_descartados'):
        log('lidos: %d | descartados na limpeza: %s' % (info['documentos_lidos'],
            ', '.join('%s=%d' % kv for kv in sorted(info['documentos_descartados'].items()))))
    log('documentos: %d | tokens: %d | tokens distintos: %d | %.1f%% do texto removido na limpeza'
        % (info['documentos'], info['tokens'], info['vocabulario_tokens_distintos'],
           info['percentual_removido_limpeza']))
    log('tempo: %.0f s (leitura %.0f s, limpeza %.0f s, tokenizacao %.0f s)'
        % (info['tempo_total_s'], info['tempo_leitura_json_s'], info['tempo_limpeza_s'],
           info['tempo_tokenizacao_s']))


def cmd_construir(args):
    if not os.path.exists(os.path.join(dir_preparo(args), 'tokens.jsonl.gz')):
        cmd_preparar(args)
    norm = preproc.Normalizador(not args.sem_stopwords, not args.sem_stemming)
    log('Construindo o indice "%s": %s, granularidade %s%s, blocos de %d docs, %d buckets'
        % (args.nome, norm.descricao(), args.granularidade,
           ' (%d termos)' % args.trecho if args.granularidade == 'trecho' else '',
           args.bloco, args.buckets))
    _, est = construtor.construir(norm, dir_preparo(args), dir_idx(args, args.nome),
                                  args.granularidade, args.trecho, args.bloco, args.buckets,
                                  args.manter_blocos, log, args.nome,
                                  posicional=not args.sem_posicoes)
    log('pronto: %d termos, %d postings, indice com %.1f MB (%.1f MB com as posicoes), %.0f s'
        % (est['vocabulario'], est['postings'], est['tamanho_indice_bytes'] / 1048576.0,
           est.get('tamanho_indice_com_posicoes_bytes', 0) / 1048576.0,
           est['tempo_indexacao_total_s']))


def cmd_fundir(args):
    """Refaz só a fusão de um índice com outro número de buckets (blocos mantidos)."""
    origem = dir_idx(args, args.origem)
    destino = dir_idx(args, args.nome)
    if not os.path.isdir(destino):
        os.makedirs(destino)
    shutil.copy(os.path.join(origem, 'unidades.tsv'), destino)
    est = construtor.fundir(destino, args.buckets, os.path.join(origem, 'blocos'), log)
    with io.open(os.path.join(origem, 'config.json'), encoding='utf-8') as fp:
        meta = json.load(fp)
    meta['config']['buckets'] = args.buckets
    meta['config']['nome'] = args.nome
    meta['estatisticas'].update(est)
    with io.open(os.path.join(destino, 'config.json'), 'w', encoding='utf-8') as fp:
        json.dump(meta, fp, ensure_ascii=False, indent=1)
    log('fusao em %d buckets: %.1f s' % (args.buckets, est['tempo_fusao_s']))


def cmd_buscar(args):
    import re as _re
    from riw_indice.busca import Buscador
    bus = Buscador(dir_idx(args, args.indice))
    res = bus.buscar(args.consulta, por_pagina=args.k, modo=args.modo, ano=[args.ano] if args.ano else None,
                     modelo=args.modelo, reforco_2026=0 if args.sem_reforco else 0.3)
    log('consulta: %s  ->  termos: %s%s' % (args.consulta, ' '.join(res['termos']),
        ''.join('  | frase: "%s"' % ' '.join(f) for f in res['frases'])))
    log('%d documentos encontrados em %.1f ms (%d postings lidas, modelo %s)\n'
        % (res['total'], res['tempo_busca_ms'], res['postings_lidas'], args.modelo))
    for r in res['resultados']:
        log('%2d. [%.3f] %s (%s, %s)\n    %s\n    %s' % (
            r['posicao'], r['nota'], r['titulo'][:110], r['fonte'], r['ano'] or 's/ data', r['url'],
            _re.sub('</?mark>', '*', r['trecho'])[:220].replace('&quot;', '"').replace('&#x27;', "'")))


def cmd_info(args):
    caminho = os.path.join(dir_idx(args, args.indice), 'config.json')
    with io.open(caminho, encoding='utf-8') as fp:
        meta = json.load(fp)
    for bloco in ('config', 'estatisticas'):
        log('== %s' % bloco)
        for k, v in meta[bloco].items():
            if not isinstance(v, list):
                log('  %-34s %s' % (k, v))


# ---------------------------------------------------------------------------
# experimentos
# ---------------------------------------------------------------------------
def _rodar(args, *extra):
    cmd = [sys.executable, os.path.abspath(__file__), '--base', args.base, '--colecao', args.colecao]
    if args.csv:
        cmd += ['--csv', args.csv]
    cmd += list(extra)
    log('\n$ indexar.py ' + ' '.join(extra))
    r = subprocess.call(cmd)
    if r != 0:
        raise SystemExit('falhou: %s' % ' '.join(extra))


def _meta(args, nome):
    with io.open(os.path.join(dir_idx(args, nome), 'config.json'), encoding='utf-8') as fp:
        return json.load(fp)


def _avaliar_consultas(args, nome, guardar_resultados=None):
    """Latência média das consultas (vocabulário frio: índice aberto do zero)."""
    from riw_indice.leitor import Indice
    t0 = time.time()
    idx = Indice(dir_idx(args, nome))
    t_abrir = time.time() - t0
    tempos, lidas, carregados = [], [], []
    for q in CONSULTAS:
        idx._vocab = {}       # força a leitura do vocabulário do disco em toda consulta
        termos, res, n, t = idx.buscar(q, 10)
        tempos.append(t)
        lidas.append(n)
        carregados.append(sum(len(v) for v in idx._vocab.values()))
        if guardar_resultados is not None:
            docs = idx.documentos()
            for pos, (d, (nota, u)) in enumerate(res[:5], 1):
                guardar_resultados.append([nome, q, ' '.join(termos), pos, round(nota, 4),
                                           docs[d]['titulo'][:150], docs[d]['fonte'],
                                           docs[d]['ano'], docs[d]['url']])
    return {
        'tempo_abrir_indice_s': round(t_abrir, 3),
        'consulta_tempo_medio_ms': round(1000 * sum(tempos) / len(tempos), 1),
        'consulta_tempo_max_ms': round(1000 * max(tempos), 1),
        'consulta_postings_lidas_media': int(sum(lidas) / len(lidas)),
        'consulta_termos_vocab_carregados_media': int(sum(carregados) / len(carregados)),
    }


def _disco(caminho, cluster=4096):
    """Espaço ocupado no disco (arquivos arredondados para o cluster de 4 KB do NTFS)."""
    total = arquivos = 0
    for atual, pastas, arqs in os.walk(caminho):
        pastas[:] = [p for p in pastas if p != 'blocos']
        for a in arqs:
            if a == 'config.json':
                continue
            tam = os.path.getsize(os.path.join(atual, a))
            total += int(math.ceil(tam / float(cluster))) * cluster
            arquivos += 1
    return total, arquivos


def cmd_experimentos(args):
    t_ini = time.time()
    res_dir = os.path.join(args.base, 'resultados')
    if not os.path.isdir(res_dir):
        os.makedirs(res_dir)
    if args.refazer_preparo or not os.path.exists(os.path.join(dir_preparo(args), 'tokens.jsonl.gz')):
        cmd_preparar(args)
    with io.open(os.path.join(dir_preparo(args), 'preparo.json'), encoding='utf-8') as fp:
        preparo = json.load(fp)
    shutil.copy(os.path.join(dir_preparo(args), 'preparo.json'), res_dir)

    # 1) pré-processamento x granularidade (cada um em processo separado: memória limpa)
    principais = [
        ('base', ['--sem-stopwords', '--sem-stemming']),
        ('stopwords', ['--sem-stemming']),
        ('padrao', ['--manter-blocos']),
        ('trecho100', ['--granularidade', 'trecho', '--trecho', '100']),
        ('trecho250', ['--granularidade', 'trecho', '--trecho', '250']),
        ('trecho500', ['--granularidade', 'trecho', '--trecho', '500']),
    ]
    linhas = []
    exemplos = []
    for nome, extra in principais:
        _rodar(args, 'construir', '--nome', nome, *extra)
        meta = _meta(args, nome)
        meta['estatisticas'].update(_avaliar_consultas(args, nome, exemplos if nome == 'padrao' else None))
        linhas.append((nome, 'pre-processamento/granularidade', meta))

    # 2) tamanho do bloco SPIMI (mesma configuração do padrão)
    for bloco in args.blocos:
        if bloco == 10000:
            continue
        nome = 'bloco%d' % bloco
        _rodar(args, 'construir', '--nome', nome, '--bloco', str(bloco))
        linhas.append((nome, 'tamanho do bloco', _meta(args, nome)))
        shutil.rmtree(dir_idx(args, nome), ignore_errors=True)

    # 3) número de buckets (refaz só a fusão a partir dos blocos do padrão)
    for b in args.buckets_testar:
        nome = 'buckets%d' % b
        if b == 64:
            meta = _meta(args, 'padrao')
            meta['estatisticas'].update(_avaliar_consultas(args, 'padrao'))
        else:
            _rodar(args, 'fundir', '--origem', 'padrao', '--nome', nome, '--buckets', str(b))
            meta = _meta(args, nome)
            meta['estatisticas'].update(_avaliar_consultas(args, nome))
        disco, arquivos = _disco(dir_idx(args, 'padrao' if b == 64 else nome))
        meta['estatisticas']['espaco_em_disco_bytes'] = disco
        meta['estatisticas']['arquivos'] = arquivos
        linhas.append((nome, 'numero de buckets', meta))
        if b != 64:
            shutil.rmtree(dir_idx(args, nome), ignore_errors=True)
    shutil.rmtree(os.path.join(dir_idx(args, 'padrao'), 'blocos'), ignore_errors=True)

    # --- grava resultados ----------------------------------------------------
    colunas_cfg = ['granularidade', 'tam_trecho', 'stopwords', 'stemming', 'bloco_docs', 'buckets']
    colunas_est = ['documentos', 'unidades', 'termos_indexados', 'vocabulario', 'postings',
                   'postings_por_termo', 'blocos', 'tempo_normalizacao_s', 'tempo_inversao_s',
                   'tempo_escrita_blocos_s', 'tempo_fase1_s', 'tempo_fusao_s',
                   'tempo_indexacao_total_s', 'memoria_pico_mb', 'memoria_pico_fusao_mb',
                   'tamanho_blocos_bytes', 'tamanho_vocabulario_bytes', 'tamanho_postings_bytes',
                   'tamanho_unidades_bytes', 'tamanho_indice_bytes', 'postings_sem_compressao_bytes',
                   'razao_compressao', 'bytes_por_posting', 'bucket_postings_min_bytes',
                   'bucket_postings_max_bytes', 'bucket_postings_medio_bytes',
                   'espaco_em_disco_bytes', 'arquivos', 'tempo_abrir_indice_s',
                   'consulta_tempo_medio_ms', 'consulta_tempo_max_ms',
                   'consulta_postings_lidas_media', 'consulta_termos_vocab_carregados_media']
    with io.open(os.path.join(res_dir, 'experimentos.csv'), 'w', encoding='utf-8-sig', newline='') as fp:
        w = csv.writer(fp, delimiter=';')
        w.writerow(['experimento', 'grupo'] + colunas_cfg + colunas_est)
        for nome, grupo, meta in linhas:
            w.writerow([nome, grupo] + [meta['config'].get(c) for c in colunas_cfg]
                       + [meta['estatisticas'].get(c) for c in colunas_est])
    with io.open(os.path.join(res_dir, 'experimentos.json'), 'w', encoding='utf-8') as fp:
        json.dump([{'experimento': n, 'grupo': g, 'config': m['config'],
                    'estatisticas': m['estatisticas']} for n, g, m in linhas],
                  fp, ensure_ascii=False, indent=1)
    with io.open(os.path.join(res_dir, 'consultas_exemplos.csv'), 'w', encoding='utf-8-sig', newline='') as fp:
        w = csv.writer(fp, delimiter=';')
        w.writerow(['indice', 'consulta', 'termos', 'posicao', 'nota', 'titulo', 'fonte', 'ano', 'url'])
        w.writerows(exemplos)

    # Heaps: tokens brutos + cada configuração principal
    with io.open(os.path.join(res_dir, 'heaps.csv'), 'w', encoding='utf-8-sig', newline='') as fp:
        w = csv.writer(fp, delimiter=';')
        w.writerow(['configuracao', 'termos_vistos', 'vocabulario'])
        for n, v in preparo['heaps_tokens_vocabulario']:
            w.writerow(['tokens_brutos', n, v])
        for nome, grupo, meta in linhas:
            if nome in ('base', 'stopwords', 'padrao'):
                for n, v in meta['estatisticas']['heaps_termos_vocabulario']:
                    w.writerow([nome, n, v])

    # Zipf: frequência de cada termo (cf) ordenada, para base e padrao
    with io.open(os.path.join(res_dir, 'zipf.csv'), 'w', encoding='utf-8-sig', newline='') as fp:
        w = csv.writer(fp, delimiter=';')
        w.writerow(['configuracao', 'posicao', 'termo', 'frequencia', 'df'])
        for nome in ('base', 'padrao'):
            termos = []
            pasta = os.path.join(dir_idx(args, nome), 'vocabulario')
            for arq in os.listdir(pasta):
                with io.open(os.path.join(pasta, arq), encoding='utf-8') as f:
                    for ln in f:
                        t, df, cf, _, _ = ln.rstrip('\n').split('\t')
                        termos.append((int(cf), int(df), t))
            termos.sort(reverse=True)
            for i, (cf, df, t) in enumerate(termos, 1):
                if i <= 1000 or i % 50 == 0:
                    w.writerow([nome, i, t, cf, df])
    log('\nExperimentos concluidos em %.1f min. Resultados em %s' % ((time.time() - t_ini) / 60, res_dir))
    log('Mande a pasta "resultados" inteira (e pode compactar em .zip).')


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--base', default=BASE, help=argparse.SUPPRESS)
    ap.add_argument('--colecao', default=COLECAO, help=argparse.SUPPRESS)
    ap.add_argument('--csv', default=None,
                    help='usar o CSV unificado (ex.: corpus_eleicoes_2026_final.csv) em vez dos lotes')
    sub = ap.add_subparsers(dest='comando')

    p = sub.add_parser('preparar', help='limpeza + analise lexica (gera a colecao tokenizada)')
    p.add_argument('--limite-lotes', type=int, default=None)
    p.set_defaults(func=cmd_preparar)

    p = sub.add_parser('construir', help='constroi um indice invertido')
    p.add_argument('--nome', default='padrao')
    p.add_argument('--granularidade', choices=['documento', 'trecho'], default='documento')
    p.add_argument('--trecho', type=int, default=250, help='tamanho do trecho (termos)')
    p.add_argument('--sem-stopwords', action='store_true')
    p.add_argument('--sem-stemming', action='store_true')
    p.add_argument('--bloco', type=int, default=10000, help='documentos por bloco SPIMI')
    p.add_argument('--buckets', type=int, default=64)
    p.add_argument('--manter-blocos', action='store_true')
    p.add_argument('--sem-posicoes', action='store_true', help='nao grava o indice posicional')
    p.add_argument('--limite-lotes', type=int, default=None)
    p.set_defaults(func=cmd_construir)

    p = sub.add_parser('fundir', help=argparse.SUPPRESS)
    p.add_argument('--origem', required=True)
    p.add_argument('--nome', required=True)
    p.add_argument('--buckets', type=int, required=True)
    p.set_defaults(func=cmd_fundir)

    p = sub.add_parser('experimentos', help='roda todas as variacoes e grava os resultados')
    p.add_argument('--limite-lotes', type=int, default=None, help='so para teste rapido')
    p.add_argument('--refazer-preparo', action='store_true')
    p.add_argument('--blocos', type=int, nargs='+', default=[2000, 10000, 50000])
    p.add_argument('--buckets-testar', type=int, nargs='+', default=[1, 16, 64, 256, 1024])
    p.set_defaults(func=cmd_experimentos)

    p = sub.add_parser('buscar', help='consulta de teste no indice')
    p.add_argument('consulta')
    p.add_argument('--indice', default='padrao')
    p.add_argument('--ano', default=None)
    p.add_argument('-k', type=int, default=10)
    p.add_argument('--modo', choices=['ou', 'e'], default='ou')
    p.add_argument('--modelo', choices=['bm25', 'tfidf'], default='bm25')
    p.add_argument('--sem-reforco', action='store_true', help='sem o reforco para documentos de 2026')
    p.set_defaults(func=cmd_buscar)

    p = sub.add_parser('info', help='configuracao e estatisticas de um indice')
    p.add_argument('--indice', default='padrao')
    p.set_defaults(func=cmd_info)

    args = ap.parse_args()
    if not getattr(args, 'func', None):
        ap.print_help()
        return
    args.func(args)


if __name__ == '__main__':
    main()
