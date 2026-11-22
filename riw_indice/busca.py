# -*- coding: utf-8 -*-
"""
Recuperação e ranqueamento (Etapa 3).

Fluxo de uma consulta:
  1. ANÁLISE DA CONSULTA - o texto passa pela MESMA análise léxica e
     normalização dos documentos (minúsculas, stopwords, stemming, sem acento).
     Sintaxe aceita:
        palavras soltas        reforma tributaria
        frase exata            "segurança pública"
        excluir um termo       saude -dengue
  2. RECUPERAÇÃO - lê do índice invertido só as listas de postings dos termos
     da consulta (um bucket por termo) e acumula a nota BM25 de cada documento.
     Modo 'ou' (padrão): basta ter um dos termos. Modo 'e': precisa ter todos.
  3. FRASES - para cada frase entre aspas, os candidatos são cruzados com o
     índice POSICIONAL: o documento só fica se os termos aparecem na mesma
     ordem e distância da consulta.
  4. FILTROS - ano, fonte e origem (mídia / institucional), lidos do cadastro
     de documentos (documentos.tsv).
  5. REORDENAÇÃO dos melhores candidatos:
        x (1 + peso_titulo * fração dos termos da consulta presentes no título)
        x (1 + reforco_2026) se o documento é de 2026 (o tema do buscador)
  6. TRECHOS - para os resultados da página, o texto limpo do documento é lido
     do arquivo textos.bin e a frase com mais termos da consulta vira o trecho,
     com as palavras encontradas destacadas.

BM25 (Robertson e Zaragoza, 2009), para o documento d e a consulta q:

    score(d, q) = soma sobre t em q de  IDF(t) * tf(t,d) * (k1 + 1)
                                         --------------------------------------
                                         tf(t,d) + k1 * (1 - b + b * |d| / avgdl)

    IDF(t) = ln(1 + (N - df(t) + 0,5) / (df(t) + 0,5))

    N = número de documentos, df = documentos com o termo, |d| = número de
    termos do documento (sem stopwords), avgdl = tamanho médio.
    k1 controla a saturação do tf; b controla a normalização pelo tamanho.
"""
from __future__ import unicode_literals, division

import array
import heapq
import html
import io
import math
import os
import re
import time

from . import preproc
from .corpus import Textos
from .leitor import Indice

K1 = 0.6          # escolhido na avaliação (avaliar.py): melhor que o clássico 1,2 nesta coleção
B = 0.75
PESO_TITULO = 0.5
REFORCO_2026 = 0.3
CANDIDATOS = 200          # quantos candidatos passam pela reordenação

_ASPAS = re.compile(r'"([^"]+)"|“([^”]+)”')
_FRASES_TEXTO = re.compile(r'(?<=[.!?;])\s+|\n+')


class Consulta(object):
    """Resultado da análise da consulta."""

    def __init__(self, texto, norm):
        self.texto = texto
        self.frases = []          # cada frase: [(termo, deslocamento), ...]
        self.excluir = set()
        termos = []
        resto = texto
        for m in _ASPAS.finditer(texto):
            frase = m.group(1) or m.group(2)
            toks = preproc.tokenizar(frase)
            ft = []
            for i, tok in enumerate(toks):
                t = norm.termo(tok)
                if t is not None:
                    ft.append((t, i))
            if len(ft) > 1:
                base = ft[0][1]
                self.frases.append([(t, d - base) for t, d in ft])
            termos.extend(t for t, _ in ft)
        resto = _ASPAS.sub(' ', texto)
        for palavra in resto.split():
            if palavra.startswith('-') and len(palavra) > 1:
                for tok in preproc.tokenizar(palavra[1:]):
                    t = norm.termo(tok)
                    if t:
                        self.excluir.add(t)
                continue
            for tok in preproc.tokenizar(palavra):
                t = norm.termo(tok)
                if t is not None:
                    termos.append(t)
        # mantém a ordem e tira repetidos
        vistos = set()
        self.termos = [t for t in termos if not (t in vistos or vistos.add(t))]

    def vazia(self):
        return not self.termos


class Buscador(object):
    def __init__(self, dir_indice):
        self.indice = Indice(dir_indice)
        self.norm = self.indice.norm
        self.textos = Textos(self.indice.config['dir_preparo'])
        self._carregar_documentos()
        self._titulos = {}

    # -- cadastro dos documentos ----------------------------------------------
    def _carregar_documentos(self):
        caminho = os.path.join(self.indice.config['dir_preparo'], 'documentos.tsv')
        self.url, self.titulo, self.fonte, self.tipo = [], [], [], []
        self.ano = array.array('h')
        self.origem = bytearray()                 # 0 = mídia, 1 = institucional
        fontes = {}
        with io.open(caminho, encoding='utf-8') as fp:
            fp.readline()
            for ln in fp:
                c = ln.rstrip('\n').split('\t')
                self.url.append(c[2])
                self.titulo.append(c[3])
                f = fontes.setdefault(c[4], c[4])   # uma cópia de cada nome de fonte
                self.fonte.append(f)
                self.ano.append(int(c[5]) if c[5].isdigit() else 0)
                self.tipo.append(c[6])
                self.origem.append(1 if c[7] == 'institucional' else 0)
        self.n_docs = len(self.url)

    def fontes(self, minimo=20):
        cont = {}
        for f in self.fonte:
            cont[f] = cont.get(f, 0) + 1
        return sorted(((f, n) for f, n in cont.items() if f and n >= minimo), key=lambda x: -x[1])

    def anos(self):
        cont = {}
        for a in self.ano:
            cont[a] = cont.get(a, 0) + 1
        return sorted(((a, n) for a, n in cont.items() if a), reverse=True)

    def termos_titulo(self, d):
        t = self._titulos.get(d)
        if t is None:
            t = self._titulos[d] = frozenset(self.norm.termos(preproc.tokenizar(self.titulo[d])))
        return t

    # -- frase exata (índice posicional) ---------------------------------------
    def _documentos_com_frase(self, frase, candidatos):
        """Unidades de `candidatos` em que os termos aparecem na ordem/distância da frase."""
        ok = set(candidatos)
        listas = []
        # começa pelo termo mais raro: reduz os candidatos antes de ler os comuns
        for t, desl in sorted(frase, key=lambda td: (self.indice.entrada(td[0]) or (0,))[0]):
            if not ok:
                return set()
            pos = self.indice.posicoes(t, ok)
            ok &= set(pos)
            listas.append((desl, pos))
        resultado = set()
        for u in ok:
            desl0, pos0 = listas[0]
            inicios = set(p - desl0 for p in pos0[u])
            for desl, pos in listas[1:]:
                inicios &= set(p - desl for p in pos[u])
                if not inicios:
                    break
            if inicios:
                resultado.add(u)
        return resultado

    # -- busca -----------------------------------------------------------------
    def buscar(self, texto, pagina=1, por_pagina=10, modo='ou', ano=None, fonte=None, origem=None,
               modelo='bm25', k1=K1, b=B, peso_titulo=PESO_TITULO, reforco_2026=REFORCO_2026,
               com_trechos=True):
        t0 = time.time()
        idx = self.indice
        q = Consulta(texto, self.norm)
        res = {'consulta': texto, 'termos': q.termos, 'frases': [[t for t, _ in f] for f in q.frases],
               'excluidos': sorted(q.excluir), 'total': 0, 'resultados': [], 'pagina': pagina,
               'por_pagina': por_pagina, 'postings_lidas': 0, 'termos_ausentes': []}
        if q.vazia():
            res['tempo_ms'] = round((time.time() - t0) * 1000, 1)
            return res

        # 2. recuperação + BM25 (ou TF-IDF da Etapa 2, para comparação)
        N = idx.N
        avgdl = idx.media_len
        notas = {}
        presentes = {}           # unidade -> nº de termos da consulta presentes
        unid_len = idx.unid_len
        termos_ok = []
        for t in q.termos:
            e = idx.entrada(t)
            if e is None:
                res['termos_ausentes'].append(t)
                continue
            termos_ok.append(t)
            df = e[0]
            res['postings_lidas'] += df
            if modelo == 'tfidf':
                idf = math.log(N / df)
                for u, tf in idx.postings(t):
                    notas[u] = notas.get(u, 0.0) + (1 + math.log(tf)) * idf / math.sqrt(max(unid_len[u], 1))
                    presentes[u] = presentes.get(u, 0) + 1
                continue
            idf = math.log(1 + (N - df + 0.5) / (df + 0.5))
            k1b = k1 * (1 - b)
            k1b_avg = k1 * b / avgdl
            fator = idf * (k1 + 1)
            g = notas.get
            for u, tf in idx.postings(t):
                notas[u] = g(u, 0.0) + fator * tf / (tf + k1b + k1b_avg * unid_len[u])
                presentes[u] = presentes.get(u, 0) + 1
        if modo == 'e':
            precisa = len(q.termos)
            notas = dict((u, s) for u, s in notas.items() if presentes[u] >= precisa)
        # exclusões
        for t in q.excluir:
            if notas and idx.entrada(t):
                for u, _ in idx.postings(t):
                    notas.pop(u, None)
        # 3. frases exatas
        for frase in q.frases:
            if not notas:
                break
            if not idx.posicional:
                res['aviso'] = 'indice sem posicoes: frase tratada como palavras soltas'
                break
            ok = self._documentos_com_frase(frase, notas.keys())
            notas = dict((u, notas[u]) for u in ok)

        # documento = melhor unidade (para índice por trechos)
        unid_doc = idx.unid_doc
        por_doc = {}
        for u, s in notas.items():
            d = unid_doc[u]
            ant = por_doc.get(d)
            if ant is None or s > ant[0]:
                por_doc[d] = (s, u)
        # contagens por ano / origem / fonte (antes dos filtros) para a interface
        f_ano, f_origem, f_fonte = {}, [0, 0], {}
        for d in por_doc:
            f_ano[self.ano[d]] = f_ano.get(self.ano[d], 0) + 1
            f_origem[self.origem[d]] += 1
            f_fonte[self.fonte[d]] = f_fonte.get(self.fonte[d], 0) + 1
        res['facetas'] = {
            'ano': sorted(([a, n] for a, n in f_ano.items() if a), reverse=True),
            'sem_ano': f_ano.get(0, 0),
            'origem': {'midia': f_origem[0], 'institucional': f_origem[1]},
            'fonte': sorted(([f, n] for f, n in f_fonte.items() if f), key=lambda x: -x[1])[:15],
        }
        # 4. filtros
        anos = set(int(a) for a in ano) if ano else None
        if anos is not None or fonte or origem is not None:
            por_doc = dict((d, v) for d, v in por_doc.items()
                           if (anos is None or self.ano[d] in anos)
                           and (not fonte or self.fonte[d] == fonte)
                           and (origem is None or self.origem[d] == origem))
        res['total'] = len(por_doc)

        # 5. reordenação dos melhores candidatos (título e ano 2026)
        n_cand = max(CANDIDATOS, pagina * por_pagina)
        cand = heapq.nlargest(n_cand, por_doc.items(), key=lambda kv: kv[1][0])
        tq = set(termos_ok)
        final = []
        for d, (s, u) in cand:
            base = s
            if peso_titulo and tq:
                s *= 1 + peso_titulo * len(tq & self.termos_titulo(d)) / len(tq)
            if reforco_2026 and self.ano[d] == 2026:
                s *= 1 + reforco_2026
            final.append((s, base, d, u))
        final.sort(key=lambda x: -x[0])
        ini = (pagina - 1) * por_pagina
        res['tempo_busca_ms'] = round((time.time() - t0) * 1000, 1)

        # 6. trechos
        destacar = set(q.termos)
        for pos_rank, (s, base, d, u) in enumerate(final[ini:ini + por_pagina], ini + 1):
            item = {'posicao': pos_rank, 'docid': d, 'nota': round(s, 3), 'nota_bm25': round(base, 3),
                    'titulo': self.titulo[d] or self.url[d], 'url': self.url[d], 'fonte': self.fonte[d],
                    'ano': self.ano[d] or None, 'tipo': self.tipo[d],
                    'origem': 'institucional' if self.origem[d] else 'midia'}
            if com_trechos:
                item['trecho'] = self.trecho(d, destacar)
                item['titulo_html'] = self.destacar(item['titulo'], destacar)
            res['resultados'].append(item)
        res['tempo_ms'] = round((time.time() - t0) * 1000, 1)
        return res

    # -- trechos e destaque ----------------------------------------------------
    def _normalizar_token(self, tok):
        if tok[0].isdigit():
            tok = tok.replace('.', '').replace(',', '')
        elif len(tok) < 2 or len(tok) > preproc.MAX_TOKEN:
            return None
        return self.norm.termo(tok)

    def destacar(self, texto, termos):
        """HTML do texto com <mark> nas palavras cujo termo está na consulta."""
        partes = []
        ult = 0
        baixo = texto.lower()
        for m in preproc._TOKEN.finditer(baixo):
            if self._normalizar_token(m.group()) in termos:
                partes.append(html.escape(texto[ult:m.start()]))
                partes.append('<mark>%s</mark>' % html.escape(texto[m.start():m.end()]))
                ult = m.end()
        partes.append(html.escape(texto[ult:]))
        return ''.join(partes)

    def _texto(self, docid):
        # parte dos textos da base do colega veio com aspas escapadas (\\") e
        # alguns sites mandaram entidades HTML (&#8220;, &quot;...) no texto
        texto = self.textos.ler(docid).replace('\\"', '"').replace("\\'", "'")
        return html.unescape(texto)

    def trecho(self, docid, termos, tamanho=320):
        texto = self._texto(docid)
        corpo = texto.split('\n', 1)[1] if '\n' in texto else texto
        frases = [f.strip() for f in _FRASES_TEXTO.split(corpo) if len(f.strip()) > 20]
        if not frases:
            frases = [corpo]
        melhor, melhor_nota, melhor_i = frases[0], -1, 0
        for i, f in enumerate(frases):
            achados = []
            for m in preproc._TOKEN.finditer(f.lower()):
                t = self._normalizar_token(m.group())
                if t in termos:
                    achados.append(t)
            nota = len(set(achados)) + 0.1 * len(achados) - 0.0005 * i   # prefere o começo
            if nota > melhor_nota:
                melhor, melhor_nota, melhor_i = f, nota, i
        # frase curta: junta a seguinte; frase longa: recorta em volta do 1º termo
        if len(melhor) < tamanho * 0.6 and melhor_i + 1 < len(frases):
            melhor = melhor + ' ' + frases[melhor_i + 1]
        if len(melhor) > tamanho:
            # janela de `tamanho` caracteres com mais termos distintos da consulta
            achados = [(m.start(), self._normalizar_token(m.group()))
                       for m in preproc._TOKEN.finditer(melhor.lower())]
            achados = [(p, t) for p, t in achados if t in termos]
            ini, melhor_j = 0, -1
            for p, _ in achados:
                a0 = max(0, p - 60)
                dentro = [(q, t) for q, t in achados if a0 <= q < a0 + tamanho - 20]
                nota_j = len(set(t for _, t in dentro)) + 0.1 * len(dentro)
                if nota_j > melhor_j:
                    ini, melhor_j = a0, nota_j
            ini = melhor.rfind(' ', 0, ini) + 1 if ini else 0
            fim = melhor.find(' ', ini + tamanho)
            fim = len(melhor) if fim < 0 else fim
            melhor = ('… ' if ini else '') + melhor[ini:fim] + (' …' if fim < len(melhor) else '')
        return self.destacar(melhor, termos)

    def documento(self, docid, consulta=''):
        termos = set(Consulta(consulta, self.norm).termos) if consulta else set()
        texto = self._texto(docid)
        corpo = texto.split('\n', 1)[1] if '\n' in texto else texto
        paragrafos = [self.destacar(p, termos) for p in corpo.split('\n') if p.strip()]
        return {'docid': docid, 'titulo': self.titulo[docid], 'url': self.url[docid],
                'fonte': self.fonte[docid], 'ano': self.ano[docid] or None, 'tipo': self.tipo[docid],
                'origem': 'institucional' if self.origem[docid] else 'midia', 'paragrafos': paragrafos}
