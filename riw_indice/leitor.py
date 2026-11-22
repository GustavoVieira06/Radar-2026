# -*- coding: utf-8 -*-
"""
Leitura do índice em disco.

Indice carrega o vocabulário por bucket (sob demanda) e lê as postings e as
posições com um seek no arquivo do bucket. buscar_tfidf() é o ranqueamento
TF-IDF usado na Etapa 2 para validar o índice; na Etapa 3 ele fica como linha
de base para comparar com o BM25 (ver busca.py).
"""
from __future__ import unicode_literals, division

import array
import heapq
import io
import json
import math
import os
import time

from . import preproc
from .codificacao import decodificar_postings, decodificar_posicoes
from .construtor import bucket_de


class Indice(object):
    def __init__(self, dir_indice):
        self.dir = dir_indice
        with io.open(os.path.join(dir_indice, 'config.json'), encoding='utf-8') as fp:
            meta = json.load(fp)
        self.config = meta['config']
        self.est = meta['estatisticas']
        self.n_buckets = self.config['buckets']
        self.norm = preproc.Normalizador(self.config['stopwords'], self.config['stemming'])
        self._vocab = {}             # bucket -> {termo: (df, cf, desloc, tam, desloc_pos, tam_pos)}
        self.posicional = bool(self.config.get('posicional'))
        self._docs = None
        self.unid_doc = array.array('i')
        self.unid_len = array.array('i')
        with io.open(os.path.join(dir_indice, 'unidades.tsv'), encoding='utf-8') as fp:
            fp.readline()
            for ln in fp:
                c = ln.split('\t')
                self.unid_doc.append(int(c[1]))
                self.unid_len.append(int(c[3]))
        self.N = len(self.unid_doc)
        self.n_docs = (self.unid_doc[-1] + 1) if self.N else 0
        self.media_len = float(sum(self.unid_len)) / max(self.N, 1)

    # -- vocabulário (carregado por bucket, sob demanda) ----------------------
    def vocab_bucket(self, b):
        v = self._vocab.get(b)
        if v is None:
            v = {}
            with io.open(os.path.join(self.dir, 'vocabulario', 'bucket-%04d.tsv' % b),
                         encoding='utf-8') as fp:
                for ln in fp:
                    c = ln.rstrip('\n').split('\t')
                    v[c[0]] = tuple(int(x) for x in c[1:])
            self._vocab[b] = v
        return v

    def entrada(self, termo):
        return self.vocab_bucket(bucket_de(termo, self.n_buckets)).get(termo)

    def postings(self, termo):
        e = self.entrada(termo)
        if e is None:
            return []
        b = bucket_de(termo, self.n_buckets)
        with io.open(os.path.join(self.dir, 'postings', 'bucket-%04d.bin' % b), 'rb') as fp:
            fp.seek(e[2])
            return decodificar_postings(fp.read(e[3]))

    def posicoes(self, termo, unidades=None):
        """unidade -> lista de posições do termo (só nas unidades pedidas, se dado)."""
        e = self.entrada(termo)
        if e is None or len(e) < 6:
            return {}
        b = bucket_de(termo, self.n_buckets)
        with io.open(os.path.join(self.dir, 'posicoes', 'bucket-%04d.bin' % b), 'rb') as fp:
            fp.seek(e[4])
            buf = fp.read(e[5])
        return decodificar_posicoes(buf, self.postings(termo), unidades)

    def documentos(self):
        if self._docs is None:
            self._docs = {}
            caminho = os.path.join(self.config['dir_preparo'], 'documentos.tsv')
            with io.open(caminho, encoding='utf-8') as fp:
                fp.readline()
                for ln in fp:
                    c = ln.rstrip('\n').split('\t')
                    self._docs[int(c[0])] = {'url': c[2], 'titulo': c[3], 'fonte': c[4], 'ano': c[5]}
        return self._docs

    # -- busca TF-IDF da Etapa 2 (linha de base) -------------------------------
    def buscar(self, consulta, k=10, ano=None):
        return self.buscar_tfidf(consulta, k, ano)

    def buscar_tfidf(self, consulta, k=10, ano=None):
        t0 = time.time()
        termos = self.norm.termos(preproc.tokenizar(consulta))
        notas = {}
        lidas = 0
        for termo in set(termos):
            e = self.entrada(termo)
            if e is None:
                continue
            idf = math.log(self.N / e[0])
            for u, tf in self.postings(termo):
                notas[u] = notas.get(u, 0.0) + (1 + math.log(tf)) * idf
            lidas += e[0]
        # nota da unidade normalizada pelo tamanho; documento = melhor unidade
        por_doc = {}
        for u, s in notas.items():
            s = s / math.sqrt(max(self.unid_len[u], 1))
            d = self.unid_doc[u]
            if s > por_doc.get(d, (0, 0))[0]:
                por_doc[d] = (s, u)
        if ano:
            docs = self.documentos()
            por_doc = dict((d, v) for d, v in por_doc.items() if docs[d]['ano'] == str(ano))
        melhores = heapq.nlargest(k, por_doc.items(), key=lambda kv: kv[1][0])
        tempo = time.time() - t0
        return termos, melhores, lidas, tempo
