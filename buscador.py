#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Etapa 3 - Interface de busca (servidor web local).

Uso (dentro da pasta do projeto, com o venv ativado):
    python buscador.py                 # abre http://localhost:8026 no navegador
    python buscador.py --porta 9000 --sem-navegador

Antes, o índice precisa existir (python indexar.py --csv <corpus.csv> construir).

O servidor usa só a biblioteca padrão do Python (http.server): não há
framework web nem banco de dados. A página (pasta web/) chama a API JSON:
    GET /api/info                       estatísticas do índice, anos e fontes
    GET /api/buscar?q=...&pagina=1&modo=ou|e&ano=2026&fonte=...&origem=midia|institucional
                    &modelo=bm25|tfidf&reforco=1
    GET /api/documento?id=123&q=...     texto completo com os termos destacados
"""
from __future__ import print_function, unicode_literals

import argparse
import io
import json
import mimetypes
import os
import sys
import threading
import time
import webbrowser

try:
    from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
except ImportError:  # Python < 3.7
    from http.server import HTTPServer as ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlsplit, parse_qs

RAIZ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, RAIZ)
for _f in (sys.stdout, sys.stderr):
    try:
        _f.reconfigure(errors='replace')
    except Exception:
        pass

from riw_indice.busca import Buscador  # noqa: E402

WEB = os.path.join(RAIZ, 'web')
BUSCADOR = None
TRAVA = threading.Lock()      # o leitor usa arquivos abertos (seek): uma busca por vez
INFO = {}


def _int(v, padrao):
    try:
        return int(v)
    except (TypeError, ValueError):
        return padrao


class Manipulador(BaseHTTPRequestHandler):
    server_version = 'RIW-Buscador/1.0'

    def log_message(self, fmt, *args):
        pass  # sem log de cada requisição no terminal

    def _json(self, obj, status=200):
        corpo = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(corpo)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(corpo)

    def _arquivo(self, nome):
        caminho = os.path.normpath(os.path.join(WEB, nome))
        if not caminho.startswith(WEB) or not os.path.isfile(caminho):
            self.send_error(404)
            return
        with io.open(caminho, 'rb') as fp:
            corpo = fp.read()
        tipo = mimetypes.guess_type(caminho)[0] or 'application/octet-stream'
        if tipo.startswith('text/') or tipo.endswith('javascript'):
            tipo += '; charset=utf-8'
        self.send_response(200)
        self.send_header('Content-Type', tipo)
        self.send_header('Content-Length', str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        url = urlsplit(self.path)
        p = dict((k, v[-1]) for k, v in parse_qs(url.query).items())
        try:
            if url.path == '/api/info':
                return self._json(INFO)
            if url.path == '/api/buscar':
                anos = [a for a in p.get('ano', '').split(',') if a.isdigit()]
                origem = {'midia': 0, 'institucional': 1}.get(p.get('origem'))
                with TRAVA:
                    res = BUSCADOR.buscar(
                        p.get('q', ''), pagina=max(1, _int(p.get('pagina'), 1)),
                        por_pagina=min(50, max(1, _int(p.get('por_pagina'), 10))),
                        modo='e' if p.get('modo') == 'e' else 'ou', ano=anos or None,
                        fonte=p.get('fonte') or None, origem=origem,
                        modelo='tfidf' if p.get('modelo') == 'tfidf' else 'bm25',
                        reforco_2026=0.3 if p.get('reforco', '1') == '1' else 0.0)
                print('  busca: %-40s %6d docs  %6.1f ms' % (p.get('q', '')[:40], res['total'], res['tempo_ms']))
                return self._json(res)
            if url.path == '/api/documento':
                d = _int(p.get('id'), -1)
                if not 0 <= d < BUSCADOR.n_docs:
                    return self._json({'erro': 'documento inexistente'}, 404)
                with TRAVA:
                    return self._json(BUSCADOR.documento(d, p.get('q', '')))
            if url.path in ('/', '/index.html'):
                return self._arquivo('index.html')
            return self._arquivo(url.path.lstrip('/'))
        except Exception as e:  # erro na busca não derruba o servidor
            import traceback
            traceback.print_exc()
            return self._json({'erro': str(e)}, 500)


def main():
    global BUSCADOR, INFO
    ap = argparse.ArgumentParser(description='Interface web do buscador (Etapa 3)')
    ap.add_argument('--indice', default=os.path.join(RAIZ, 'dados', 'indice', 'indices', 'padrao'))
    ap.add_argument('--porta', type=int, default=8026)
    ap.add_argument('--sem-navegador', action='store_true')
    args = ap.parse_args()
    if not os.path.exists(os.path.join(args.indice, 'config.json')):
        raise SystemExit('Indice nao encontrado em %s\nRode antes: python indexar.py --csv '
                         'corpus_eleicoes_2026_final.csv construir' % args.indice)
    t0 = time.time()
    print('Carregando o indice de %s ...' % args.indice)
    BUSCADOR = Buscador(args.indice)
    BUSCADOR.buscar('eleicao', com_trechos=False)   # aquece o cache
    est = BUSCADOR.indice.est
    INFO = {
        'documentos': BUSCADOR.n_docs,
        'termos': est.get('vocabulario'),
        'postings': est.get('postings'),
        'tamanho_indice_mb': round(est.get('tamanho_indice_com_posicoes_bytes', est.get('tamanho_indice_bytes', 0)) / 1048576.0, 1),
        'posicional': BUSCADOR.indice.posicional,
        'anos': BUSCADOR.anos(),
        'fontes': BUSCADOR.fontes()[:60],
        'origem': {'midia': BUSCADOR.origem.count(0), 'institucional': BUSCADOR.origem.count(1)},
    }
    print('Pronto em %.1f s: %d documentos, %d termos.' % (time.time() - t0, INFO['documentos'], INFO['termos']))
    srv = ThreadingHTTPServer(('127.0.0.1', args.porta), Manipulador)
    endereco = 'http://localhost:%d' % args.porta
    print('Buscador no ar em %s  (Ctrl+C para encerrar)' % endereco)
    if not args.sem_navegador:
        threading.Timer(0.8, lambda: webbrowser.open(endereco)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\nEncerrado.')


if __name__ == '__main__':
    main()
