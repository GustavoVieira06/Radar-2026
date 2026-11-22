#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Etapa 3 - Avaliação automática do ranqueamento (sem julgamento manual).

Busca de item conhecido ("known-item search"): sorteamos documentos da coleção
e usamos palavras do TÍTULO de cada um como consulta, simulando quem lembra de
uma notícia e quer reencontrá-la. Como sabemos qual é o documento certo, dá
para medir em que posição ele aparece:

    MRR        = média de 1/posição do documento certo (0 se não estiver no top 100)
    Acerto@1   = fração das consultas em que ele veio em 1º
    Acerto@10  = fração em que ele veio entre os 10 primeiros

Variações comparadas:
    - TF-IDF da Etapa 2 (linha de base)
    - BM25 com vários k1 e b (escolha dos parâmetros) e validação da escolha
      numa segunda amostra, sorteada com outra semente
    - consultas curtas (3 palavras) e longas (título inteiro)
E o tempo das 20 consultas de exemplo da Etapa 2 com cada modelo.

Uso:  python avaliar.py [--amostra 300] [--indice padrao]
Saída: resultados/avaliacao.json e resultados/avaliacao.csv
"""
from __future__ import print_function, unicode_literals, division

import argparse
import csv
import io
import json
import os
import random
import statistics
import sys
import time

RAIZ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, RAIZ)

from riw_indice import preproc  # noqa: E402
from riw_indice.busca import Buscador  # noqa: E402
from indexar import CONSULTAS  # noqa: E402


def montar_consultas(bus, n, seed=2026):
    """Sorteia documentos com título útil e monta as consultas de item conhecido."""
    rnd = random.Random(seed)
    vistos = {}
    for d, t in enumerate(bus.titulo):
        vistos.setdefault(t.strip().lower(), []).append(d)
    ids = list(range(bus.n_docs))
    rnd.shuffle(ids)
    curtas, longas = [], []
    for d in ids:
        titulo = bus.titulo[d].split(' | ')[0].split(' - ')[0].strip()
        palavras = [w for w in preproc.tokenizar(titulo)
                    if preproc.sem_acento(w) not in preproc.STOPWORDS and not w[0].isdigit()]
        if len(palavras) < 5:
            continue
        # documentos com o mesmo título (republicações) também contam como acerto
        certos = set(vistos[bus.titulo[d].strip().lower()])
        sorteadas = rnd.sample(palavras, 3)
        curtas.append((' '.join(sorteadas), certos))
        longas.append((titulo, certos))
        if len(curtas) >= n:
            break
    return curtas, longas


def medir(bus, consultas, **kw):
    rr, a1, a10, tempos = [], 0, 0, []
    for q, certos in consultas:
        res = bus.buscar(q, por_pagina=100, com_trechos=False, peso_titulo=0, reforco_2026=0, **kw)
        tempos.append(res['tempo_busca_ms'])
        pos = next((r['posicao'] for r in res['resultados'] if r['docid'] in certos), None)
        rr.append(1.0 / pos if pos else 0.0)
        a1 += pos == 1
        a10 += bool(pos and pos <= 10)
    n = len(consultas)
    return {'mrr': round(sum(rr) / n, 4), 'acerto@1': round(a1 / n, 4), 'acerto@10': round(a10 / n, 4),
            'tempo_medio_ms': round(statistics.mean(tempos), 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--indice', default='padrao')
    ap.add_argument('--amostra', type=int, default=300)
    args = ap.parse_args()
    bus = Buscador(os.path.join(RAIZ, 'dados', 'indice', 'indices', args.indice))
    curtas, longas = montar_consultas(bus, args.amostra)
    print('%d consultas de item conhecido (ex.: "%s")' % (len(curtas), curtas[0][0]))
    linhas = []

    def reg(grupo, rotulo, consultas, **kw):
        m = medir(bus, consultas, **kw)
        m.update({'grupo': grupo, 'variacao': rotulo})
        linhas.append(m)
        print('  %-26s %-24s MRR %.3f  @1 %.3f  @10 %.3f  %6.1f ms'
              % (grupo, rotulo, m['mrr'], m['acerto@1'], m['acerto@10'], m['tempo_medio_ms']))

    for nome, cs in (('curtas', curtas), ('longas', longas)):
        print('Modelos (consultas %s):' % nome)
        reg('modelo/' + nome, 'TF-IDF (Etapa 2)', cs, modelo='tfidf')
        reg('modelo/' + nome, 'BM25 k1=1,2 b=0,75', cs, k1=1.2, b=0.75)
        reg('modelo/' + nome, 'BM25 k1=0,6 b=0,75', cs, k1=0.6, b=0.75)
    print('Parâmetros do BM25 (consultas curtas):')
    for k1 in (0.6, 0.9, 1.2, 1.5, 2.0):
        for b in (0.3, 0.5, 0.75, 0.9):
            reg('grade_bm25', 'k1=%.1f b=%.2f' % (k1, b), curtas, k1=k1, b=b)

    # validação da escolha numa 2ª amostra (outra semente), que não foi usada na grade
    curtas2, longas2 = montar_consultas(bus, args.amostra, seed=7)
    print('Validação (2ª amostra):')
    for nome, cs in (('curtas', curtas2), ('longas', longas2)):
        for k1 in (0.6, 0.9, 1.2):
            reg('validacao/' + nome, 'k1=%.1f b=0.75' % k1, cs, k1=k1, b=0.75)

    # tempo das 20 consultas da Etapa 2 + versões com frase e com modo E
    tempos = {}
    for nome, kw, fq in (('TF-IDF (Etapa 2)', {'modelo': 'tfidf'}, None),
                         ('BM25', {}, None),
                         ('BM25 modo E', {'modo': 'e'}, None),
                         ('BM25 frase exata', {}, lambda q: '"%s"' % q),
                         ('BM25 + trechos (página)', {'com_trechos': True}, None)):
        ts = []
        for _ in range(2):          # 1ª rodada aquece o cache do sistema de arquivos
            ts = []
            for q in CONSULTAS:
                t0 = time.time()
                bus.buscar(fq(q) if fq else q, com_trechos=kw.get('com_trechos', False),
                           **dict((k, v) for k, v in kw.items() if k != 'com_trechos'))
                ts.append((time.time() - t0) * 1000)
        ts.sort()
        tempos[nome] = {'media_ms': round(statistics.mean(ts), 1), 'mediana_ms': round(statistics.median(ts), 1),
                        'max_ms': round(ts[-1], 1)}
        print('  tempo %-26s média %6.1f ms  mediana %6.1f ms  máx %6.1f ms'
              % (nome, tempos[nome]['media_ms'], tempos[nome]['mediana_ms'], tempos[nome]['max_ms']))

    # exemplos de consultas para o relatório: top 3 com e sem reforço de 2026
    exemplos = []
    for q in ('reforma tributária', '"segurança pública"', 'salário mínimo', 'privatização da petrobras'):
        for ref in (0.0, 0.3):
            r = bus.buscar(q, por_pagina=3, com_trechos=False, reforco_2026=ref)
            exemplos.append({'consulta': q, 'reforco_2026': ref, 'total': r['total'],
                             'top3': [[x['titulo'][:90], x['fonte'], x['ano']] for x in r['resultados']]})

    out = os.path.join(RAIZ, 'resultados')
    if not os.path.isdir(out):
        os.makedirs(out)
    with io.open(os.path.join(out, 'avaliacao.json'), 'w', encoding='utf-8') as fp:
        json.dump({'amostra': len(curtas), 'exemplos_consultas': curtas[:15], 'resultados': linhas,
                   'tempos_20_consultas': tempos, 'exemplos_top3': exemplos},
                  fp, ensure_ascii=False, indent=1, default=list)
    with io.open(os.path.join(out, 'avaliacao.csv'), 'w', encoding='utf-8-sig', newline='') as fp:
        w = csv.writer(fp, delimiter=';')
        w.writerow(['grupo', 'variacao', 'mrr', 'acerto@1', 'acerto@10', 'tempo_medio_ms'])
        for l in linhas:
            w.writerow([l['grupo'], l['variacao'], l['mrr'], l['acerto@1'], l['acerto@10'], l['tempo_medio_ms']])
    print('gravado em resultados/avaliacao.json e .csv')


if __name__ == '__main__':
    main()
