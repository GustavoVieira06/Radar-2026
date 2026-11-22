#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Lançador do coletor (pyspider) com armazenamento 100% em arquivo.

Uso:
    python coletar.py iniciar [--porta 5000] [--threads]   # inicia/retoma a coleta
    python coletar.py status                               # números da coleta
    python coletar.py compactar                            # compacta o journal da fila

O que ele faz:
  1. registra no pyspider os esquemas `file+taskdb://` e `file+resultdb://`
     (implementados em riw/filedb.py);
  2. compacta o journal da fila antes de começar (retomada rápida);
  3. chama o pyspider (`pyspider all`) apontando os três "bancos" para arquivos:
       taskdb    -> dados/estado/       (journal JSONL)
       resultdb  -> dados/coleta/       (lotes JSONL + manifesto)
       projectdb -> projetos/*.py       (modo local nativo do pyspider)
"""
from __future__ import print_function, unicode_literals

import argparse
import glob
import io
import json
import os
import sys
import threading
import time

RAIZ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, RAIZ)
os.chdir(RAIZ)

from riw import config as cfg  # noqa: E402
from riw.filedb import FileTaskDB, FileResultDB, compactar_taskdb  # noqa: E402


def registrar_backends():
    """Ensina o pyspider a abrir os bancos em arquivo."""
    import pyspider.database as pdb
    import pyspider.run as prun
    from six.moves.urllib.parse import urlparse

    original = pdb.connect_database

    def connect_database(url):
        partes = urlparse(url)
        esquema = partes.scheme.split('+')
        if esquema[0] == 'file':
            # não usar partes.path: no Windows 'D:\...' seria lido como servidor
            caminho = url.split('://', 1)[1]
            if esquema[-1] == 'taskdb':
                db = FileTaskDB(caminho)
            elif esquema[-1] == 'resultdb':
                db = FileResultDB(caminho)
            else:
                raise LookupError('file+%s não suportado; use local+projectdb' % esquema[-1])
            db.copy = lambda: connect_database(url)
            return db
        return original(url)

    pdb.connect_database = connect_database
    prun.connect_database = connect_database


def iniciar(args):
    for d in (cfg.DIR_DADOS, cfg.DIR_ESTADO, cfg.DIR_COLETA):
        if not os.path.isdir(d):
            os.makedirs(d)
    for projeto, (antes, depois) in compactar_taskdb(cfg.DIR_ESTADO).items():
        print('fila %s: %d operações -> %d tarefas' % (projeto, antes, depois))

    config_pyspider = os.path.join(cfg.DIR_DADOS, 'pyspider.json')
    with io.open(config_pyspider, 'w', encoding='utf-8') as fp:
        json.dump({'webui': {'port': args.porta, 'need-auth': False}}, fp)

    registrar_backends()
    from riw import agendamento, cortesia, robots
    robots.aplicar_correcao()     # robots.txt com erro não derruba o site
    cortesia.aplicar_correcao()   # limite de conexões e intervalo por site (fetcher)
    if os.environ.get('RIW_PRAZO_PERDIDA'):  # só para testes
        from pyspider.scheduler.task_queue import TaskQueue
        TaskQueue.processing_timeout = int(os.environ['RIW_PRAZO_PERDIDA'])
    if not os.environ.get('RIW_SEM_AGENDAMENTO'):
        agendamento.aplicar_correcao()  # limite de URLs em andamento por site (scheduler)
    import pyspider.run as prun

    modo = 'thread' if (args.threads or os.name == 'nt' or sys.platform == 'darwin') else 'subprocess'
    argv = [
        '--config', config_pyspider,
        '--taskdb', 'file+taskdb://' + cfg.DIR_ESTADO,
        '--resultdb', 'file+resultdb://' + cfg.DIR_COLETA,
        '--projectdb', 'local+projectdb://' + os.path.join(RAIZ, 'projetos', args.projetos),
        '--data-path', os.path.join(cfg.DIR_DADOS, 'pyspider'),
        # não usamos renderização de JavaScript: um endereço fictício impede o
        # `pyspider all` de tentar subir PhantomJS/Puppeteer
        '--phantomjs-proxy', '127.0.0.1:1',
        '--puppeteer-proxy', '127.0.0.1:1',
        'all', '--run-in', modo,
        '--fetcher-num', str(args.fetchers), '--processor-num', str(args.processadores),
    ]
    print('WebUI: http://localhost:%d  (modo %s)' % (args.porta, modo))
    threading.Thread(target=disparar_on_start, args=(args.projetos,), daemon=True).start()
    prun.cli.main(args=argv, prog_name='pyspider')


def disparar_on_start(padrao):
    """Equivale a clicar em "Run" na WebUI: agenda o on_start de cada projeto.

    Sem isso o pyspider só chamaria o on_start no próximo tique do @every
    (até 6 h depois). Como as sementes usam `age`, reiniciar o coletor não
    recoleta o que ainda está dentro do prazo de revisita.
    """
    from six.moves import xmlrpc_client
    rpc = xmlrpc_client.ServerProxy('http://127.0.0.1:23333', allow_none=True)
    projetos = [os.path.splitext(os.path.basename(p))[0]
                for p in glob.glob(os.path.join(RAIZ, 'projetos', padrao))]
    for _ in range(60):
        time.sleep(3)
        try:
            rpc.size()        # responde quando o scheduler está no ar
            time.sleep(5)     # tempo para ele carregar os projetos
            for nome in projetos:
                rpc.newtask({
                    'project': nome, 'taskid': 'on_start', 'url': 'data:,on_start',
                    'process': {'callback': 'on_start'},
                    'schedule': {'age': 0, 'priority': 9, 'force_update': True},
                })
                print('on_start disparado: %s' % nome)
            return
        except Exception:
            continue  # scheduler ainda subindo


def status(_args):
    if not os.path.isdir(cfg.DIR_COLETA):
        print('nenhuma coleta ainda')
        return
    db = FileResultDB(cfg.DIR_COLETA)
    tdb = FileTaskDB(cfg.DIR_ESTADO)
    nomes = {1: 'na fila', 2: 'sucesso', 3: 'falha', 4: 'descartada'}
    for projeto in sorted(db.projects | tdb.projects):
        print('== %s' % projeto)
        if projeto in db.projects:
            c = db._colecao(projeto)
            tipos, fontes = {}, {}
            for reg in c.indice.values():
                tipos[reg['tipo']] = tipos.get(reg['tipo'], 0) + 1
                fontes[reg['fonte']] = fontes.get(reg['fonte'], 0) + 1
            print('documentos gravados: %d' % len(c.indice))
            print('  por tipo: %s' % ', '.join('%s=%d' % kv for kv in sorted(tipos.items())))
            print('  maiores fontes:')
            for fonte, n in sorted(fontes.items(), key=lambda kv: -kv[1])[:15]:
                print('    %-40s %d' % (fonte, n))
        if projeto in tdb.projects:
            contagem = tdb.status_count(projeto)
            print('URLs na fila (taskdb): ' + ', '.join(
                '%s=%d' % (nomes.get(k, k), v) for k, v in sorted(contagem.items())))


def testar(args):
    """Baixa uma URL e mostra o que o coletor extrai e a nota de relevância."""
    import requests
    from riw import texto, urls
    url = args.url
    r = requests.get(url, headers={'User-Agent': cfg.USER_AGENT,
                                   'Accept-Language': 'pt-BR,pt;q=0.9'}, timeout=30)
    print('HTTP %s  %s  %d bytes' % (r.status_code, r.headers.get('Content-Type'), len(r.content)))
    print('permitido pelos filtros de URL:', urls.permitido(urls.normalizar(url) or url,
                                                            cfg.dominios_permitidos()))
    if r.content.startswith(b'%PDF') or urls.eh_pdf(url):
        from riw import pdfs
        print('link de PDF passaria no filtro da URL:',
              bool(urls.PDF_TEMA.search(url)),
              '(o texto do link na página também conta; aqui só a URL é avaliada)')
        inicio = pdfs.texto_inicial(r.content)
        score, termos = texto.relevancia(url.rsplit('/', 1)[-1], inicio, url)
        print('texto das 5 primeiras paginas: %d caracteres' % len(inicio))
        print('relevancia: %d (limiar %d) -> %s' % (score, cfg.LIMIAR_RELEVANCIA,
              'GRAVARIA' if score >= cfg.LIMIAR_RELEVANCIA else 'descartaria'))
        print('termos :', ', '.join(termos))
        print('--- inicio do texto extraido ---')
        print(inicio[:800])
        return
    if 'html' not in (r.headers.get('Content-Type') or ''):
        return
    r.encoding = r.encoding if 'charset' in (r.headers.get('Content-Type') or '') \
        else r.apparent_encoding
    campos = texto.extrair_html(texto.parse_html(r.text, r.content), url)
    score, termos = texto.relevancia(campos['titulo'], campos['texto'], url)
    print('titulo :', campos['titulo'])
    print('data   :', campos['data_publicacao'])
    print('texto  : %d caracteres' % len(campos['texto']))
    print('relevancia: %d (limiar %d) -> %s' % (score, cfg.LIMIAR_RELEVANCIA,
          'GRAVARIA' if score >= cfg.LIMIAR_RELEVANCIA and len(campos['texto']) >= 300
          else 'descartaria'))
    print('termos :', ', '.join(termos))
    print('--- inicio do texto extraido ---')
    print(campos['texto'][:800])


def diagnostico(args):
    """Por site: fila, prioridades, baixadas/falhas na última hora e documentos."""
    import collections
    from riw import urls
    horas = args.horas
    limite = time.time() - horas * 3600
    tdb = FileTaskDB(cfg.DIR_ESTADO)
    fila, prio, baixadas, falhas, erros = (collections.Counter() for _ in range(5))
    tentando = collections.Counter()
    for projeto in sorted(tdb.projects):
        for status in (1, 2, 3, 4):
            for t in tdb.load_tasks(status, projeto, ['url', 'schedule', 'lastcrawltime',
                                                      'track', 'status']):
                dom = urls.dominio(t.get('url') or '')
                if status == 1:
                    fila[dom] += 1
                    sch = t.get('schedule') or {}
                    prio[sch.get('priority', 0)] += 1
                    if sch.get('retried'):
                        tentando[dom] += 1
                if (t.get('lastcrawltime') or 0) < limite:
                    continue
                if status == 2:
                    baixadas[dom] += 1
                else:
                    fetch = ((t.get('track') or {}).get('fetch') or {})
                    if fetch.get('ok') is False or status in (3, 4):
                        falhas[dom] += 1
                        erros[(dom, str(fetch.get('error') or fetch.get('status_code'))[:60])] += 1
    docs = collections.Counter()
    db = FileResultDB(cfg.DIR_COLETA)
    desde = time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(limite))
    for projeto in db.projects:
        for reg in db._colecao(projeto).indice.values():
            if reg['data_coleta'] >= desde:
                docs[reg['fonte']] += 1

    print('== Fila: %d URLs  | prioridades: %s' % (sum(fila.values()), ', '.join(
        '%s=%d' % kv for kv in sorted(prio.items(), reverse=True))))
    print('%-34s %8s %10s' % ('site', 'na fila', 'retentando'))
    for dom, n in fila.most_common(15):
        print('%-34s %8d %10d' % (dom, n, tentando[dom]))
    print()
    print('== Ultima(s) %g hora(s): baixadas=%d  falhas=%d  documentos=%d' % (
        horas, sum(baixadas.values()), sum(falhas.values()), sum(docs.values())))
    print('%-34s %9s %7s %6s' % ('site', 'baixadas', 'falhas', 'docs'))
    sites = set(baixadas) | set(falhas) | set(docs)
    for dom in sorted(sites, key=lambda d: -(baixadas[d] + falhas[d]))[:20]:
        print('%-34s %9d %7d %6d' % (dom, baixadas[dom], falhas[dom], docs[dom]))
    if erros:
        print()
        print('== Erros mais comuns')
        for (dom, erro), n in erros.most_common(10):
            print('%5d  %-28s %s' % (n, dom, erro))


def anos(_args):
    """Conta os documentos por ano de publicação (metadado do site ou ano na URL)."""
    import collections
    import json as _json
    import re
    from riw.filedb import _abrir_lote
    ano_url = re.compile(r'(?<![0-9A-Za-z])(20[0-2][0-9])(?![0-9A-Za-z])')
    db = FileResultDB(cfg.DIR_COLETA)
    por_ano = collections.Counter()
    antigos_fonte = collections.Counter()
    exemplos = []
    for projeto in sorted(db.projects):
        c = db._colecao(projeto)
        vivos = set((r['lote'], r['linha']) for r in c.indice.values())
        for lote in sorted(set(r['lote'] for r in c.indice.values())):
            caminho = os.path.join(c.dir_paginas, lote)
            if not os.path.exists(caminho):
                caminho += '.gz'
            with _abrir_lote(caminho) as fp:
                for i, linha in enumerate(fp):
                    if (lote, i) not in vivos:
                        continue
                    try:
                        doc = _json.loads(linha)
                    except ValueError:
                        continue
                    data = (doc.get('data_publicacao') or '')[:4]
                    if data.isdigit():
                        ano = data
                    else:
                        achados = ano_url.findall(doc.get('url') or '')
                        ano = max(achados) if achados else 'sem data'
                    por_ano[ano] += 1
                    if ano != 'sem data' and ano < '2026':
                        antigos_fonte[doc.get('fonte')] += 1
                        if len(exemplos) < 8:
                            exemplos.append('%s  %s' % (ano, doc.get('url')))
    total = sum(por_ano.values())
    print('Documentos por ano de publicacao (total %d):' % total)
    for ano, n in sorted(por_ano.items()):
        print('  %-9s %7d  (%4.1f%%)' % (ano, n, 100.0 * n / max(total, 1)))
    if antigos_fonte:
        print('Fontes com mais documentos anteriores a 2026:')
        for fonte, n in antigos_fonte.most_common(10):
            print('  %-34s %d' % (fonte, n))
        print('Exemplos:')
        for e in exemplos:
            print('  ' + e)


def paginas(_args):
    """Páginas visitadas por tipo (conteúdo x sitemap/robots) e situação."""
    import collections
    tdb = FileTaskDB(cfg.DIR_ESTADO)
    tipos = {'pagina': 'paginas de conteudo (HTML)', 'pdf': 'PDFs',
             'sitemap': 'arquivos de sitemap', 'robots': 'robots.txt',
             'on_start': 'controle', 'candidato': 'TSE', 'listar': 'TSE', 'eleicoes': 'TSE'}
    cont = collections.Counter()
    for projeto in sorted(tdb.projects):
        for status in (1, 2, 3, 4):
            for t in tdb.load_tasks(status, projeto, ['process']):
                cb = ((t.get('process') or {}).get('callback')) or '?'
                cont[(tipos.get(cb, cb), status)] += 1
    db = FileResultDB(cfg.DIR_COLETA)
    docs = sum(len(db._colecao(p).indice) for p in db.projects)
    print('%-30s %10s %10s %10s' % ('tipo', 'baixadas', 'falharam', 'na fila'))
    for tipo in sorted(set(k[0] for k in cont)):
        print('%-30s %10d %10d %10d' % (tipo, cont[(tipo, 2)], cont[(tipo, 3)] + cont[(tipo, 4)],
                                         cont[(tipo, 1)]))
    conteudo = cont[('paginas de conteudo (HTML)', 2)] + cont[('PDFs', 2)]
    print()
    print('Paginas de conteudo visitadas (HTML + PDF): %d' % conteudo)
    print('Documentos relevantes armazenados:          %d  (%.0f%% das visitadas)' % (
        docs, 100.0 * docs / max(conteudo, 1)))


def juntar(args):
    """Junta a coleta de outro computador (pasta 'dados' dele) nesta, sem repetir."""
    import json as _json
    from riw.filedb import _abrir_lote
    origem = os.path.abspath(args.pasta_dados)
    pasta_coleta = os.path.join(origem, 'coleta')
    if not os.path.isdir(pasta_coleta):
        print('nao achei %s (informe a pasta "dados" do outro computador)' % pasta_coleta)
        return
    outra = FileResultDB(pasta_coleta)
    nossa = FileResultDB(cfg.DIR_COLETA)
    novos = repetidos = 0
    for projeto in sorted(outra.projects):
        c = outra._colecao(projeto)
        vivos = dict(((r['lote'], r['linha']), r) for r in c.indice.values())
        for lote in sorted(set(k[0] for k in vivos)):
            caminho = os.path.join(c.dir_paginas, lote)
            if not os.path.exists(caminho):
                caminho += '.gz'
            with _abrir_lote(caminho) as fp:
                for i, linha in enumerate(fp):
                    if (lote, i) not in vivos:
                        continue
                    doc = _json.loads(linha)
                    taskid, url = doc.pop('id'), doc.pop('url')
                    for campo in ('data_coleta', 'updatetime'):
                        doc.pop(campo, None)
                    doc['coletado_por'] = args.nome
                    if nossa._colecao('promessas').indice.get(taskid) is not None:
                        repetidos += 1
                        continue
                    if nossa.save('promessas', taskid, url, doc):
                        novos += 1
                    else:
                        repetidos += 1
        # PDFs
        dir_pdfs = os.path.join(c.raiz, 'pdfs')
        if os.path.isdir(dir_pdfs):
            import shutil
            destino = os.path.join(cfg.DIR_COLETA, 'promessas', 'pdfs')
            if not os.path.isdir(destino):
                os.makedirs(destino)
            for nome in os.listdir(dir_pdfs):
                if not os.path.exists(os.path.join(destino, nome)):
                    shutil.copy2(os.path.join(dir_pdfs, nome), destino)
    nossa.close()
    print('documentos novos adicionados: %d | ja existentes/duplicados: %d' % (novos, repetidos))
    print('total agora: %d' % FileResultDB(cfg.DIR_COLETA).count('promessas'))


def compactar(_args):
    for projeto, (antes, depois) in compactar_taskdb(cfg.DIR_ESTADO).items():
        print('%s: %d operações -> %d tarefas' % (projeto, antes, depois))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='comando')
    p = sub.add_parser('iniciar', help='inicia ou retoma a coleta')
    p.add_argument('--porta', type=int, default=5000, help='porta da WebUI do pyspider')
    p.add_argument('--threads', action='store_true',
                   help='roda os componentes em threads (padrão no Windows/macOS)')
    p.add_argument('--fetchers', type=int, default=1, help='nº de processos fetcher')
    p.add_argument('--processadores', type=int, default=1, help='nº de processos processor')
    p.add_argument('--projetos', default='*.py',
                   help='quais projetos rodar (padrão: todos em projetos/, ex.: promessas.py)')
    p.set_defaults(func=iniciar)
    sub.add_parser('status', help='mostra a escala da coleta').set_defaults(func=status)
    p = sub.add_parser('testar', help='mostra o que o coletor extrai de uma URL')
    p.add_argument('url')
    p.set_defaults(func=testar)
    p = sub.add_parser('diagnostico', help='fila, velocidade e falhas por site')
    p.add_argument('--horas', type=float, default=1, help='janela de tempo (padrão: 1 h)')
    p.set_defaults(func=diagnostico)
    sub.add_parser('paginas', help='paginas visitadas por tipo (escala)').set_defaults(func=paginas)
    p = sub.add_parser('juntar', help='junta a coleta de outro computador nesta')
    p.add_argument('pasta_dados', help='pasta "dados" copiada do outro computador')
    p.add_argument('--nome', default='computador-2', help='identificacao da outra coleta')
    p.set_defaults(func=juntar)
    sub.add_parser('anos', help='documentos por ano de publicacao').set_defaults(func=anos)
    sub.add_parser('compactar', help='compacta o journal da fila').set_defaults(func=compactar)
    args = ap.parse_args()
    if not getattr(args, 'func', None):
        ap.print_help()
        return
    args.func(args)


if __name__ == '__main__':
    main()
