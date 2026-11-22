#!/usr/bin/env python
# -*- encoding: utf-8 -*-
# Projeto pyspider: coletor FOCADO de promessas e planos de governo (Eleições 2026).
#
# As duas linhas abaixo são lidas pelo pyspider (local+projectdb) e definem a
# ritmo com que o scheduler despacha URLs (projeto inteiro). A cortesia com
# cada SITE é garantida à parte: no máximo 6 URLs em andamento por site
# (riw/agendamento.py) e 2 conexões com 0,5 s entre pedidos (riw/cortesia.py).
# rate: 100
# burst: 200

import hashlib
import io
import json
import os
import time

import lxml.etree
import lxml.html
from six.moves.urllib.parse import urljoin
from pyspider.libs.base_handler import *  # noqa

from riw import config as cfg, pdfs, texto, urls

PROJETO = 'promessas'
DIR_PROJETO = os.path.join(cfg.DIR_COLETA, PROJETO)
ARQ_CONTAGEM = os.path.join(DIR_PROJETO, 'contagem.json')


def _contagem(cache={'t': 0, 'n': 0}):
    """Nº de documentos gravados, lido do arquivo de contagem (cache de 3 s)."""
    agora = time.time()
    if agora - cache["t"] > 3:
        cache['t'] = agora
        try:
            with io.open(ARQ_CONTAGEM, encoding='utf-8') as fp:
                cache['n'] = json.load(fp).get('documentos', 0)
        except (IOError, OSError, ValueError):
            pass
    return cache['n']


class Handler(BaseHandler):
    crawl_config = {
        'headers': {
            'User-Agent': cfg.USER_AGENT,
            'Accept-Language': 'pt-BR,pt;q=0.9',
        },
        'robots_txt': True,                  # respeita robots.txt (fetcher do pyspider)
        'connect_timeout': cfg.TIMEOUT_CONEXAO,
        'timeout': cfg.TIMEOUT_TOTAL,
        'retries': cfg.TENTATIVAS,        # nova tentativa com espera crescente
        'max_redirects': 5,
        'itag': 'v1',                        # trocar força a recoleta de tudo
    }

    DOMINIOS = cfg.dominios_permitidos()

    # -- identificação de URLs ---------------------------------------------
    def get_taskid(self, task):
        return hashlib.md5((urls.normalizar(task['url']) or task['url'])
                           .encode('utf-8')).hexdigest()

    def meta_atingida(self):
        return _contagem() >= cfg.META_DOCUMENTOS

    def seguir(self, url, origem, prof, ancora=''):
        """Enfileira uma URL descoberta, aplicando a política de seleção."""
        if prof > cfg.MAX_PROFUNDIDADE or self.meta_atingida():
            return
        url = urls.normalizar(url)
        if not url or not urls.permitido(url, self.DOMINIOS):
            return
        salvo = {'prof': prof, 'origem': origem}
        if urls.eh_pdf(url):
            # PDF só se a URL ou o texto do link indicarem plano/proposta/eleição
            # (jornais publicam muitos editais e balanços em PDF)
            if not urls.PDF_TEMA.search(url + ' ' + texto.sem_acento(ancora or '')):
                return
            self.crawl(url, callback=self.pdf, save=salvo, priority=7,
                       age=cfg.IDADE_PDF)
        else:
            self.crawl(url, callback=self.pagina, save=salvo,
                       priority=urls.prioridade(url, ancora), age=cfg.IDADE_ARTIGO)

    # -- sementes ------------------------------------------------------------
    @every(minutes=6 * 60)
    def on_start(self):
        for url in cfg.sementes():
            self.crawl(urls.normalizar(url), callback=self.pagina,
                       save={'prof': 0, 'origem': None, 'semente': True},
                       priority=9, age=cfg.IDADE_HUB, force_update=True)
        for dom in cfg.sitemaps():
            base = dom if '://' in dom else 'https://' + dom
            self.crawl(base.rstrip('/') + '/robots.txt', callback=self.robots,
                       save={'prof': 0}, priority=9, age=24 * 3600,
                       itag='corte-' + cfg.SITEMAP_CORTE)

    # -- sitemaps (fonte extra de URLs) --------------------------------------
    # itag = data de corte: se RIW_CORTE mudar, o pyspider relê robots.txt e
    # sitemaps na hora, sem esperar o prazo de revisita (age)
    def robots(self, response):
        for linha in (response.text or '').splitlines():
            if linha.lower().startswith('sitemap:'):
                self.crawl(linha.split(':', 1)[1].strip(), callback=self.sitemap,
                           save={'prof': 1}, priority=8, age=cfg.IDADE_HUB,
                           itag='corte-' + cfg.SITEMAP_CORTE)

    def sitemap(self, response):
        """Lê sitemap (ou índice de sitemaps) e segue só URLs do tema.

        Filtro temporal (cfg.SITEMAP_CORTE, padrão 2026-01-01):
          - descarta o que tem <lastmod> anterior ao corte;
          - descarta endereços cujo ano no próprio endereço é anterior ao corte
            (ex.: sitemap.xml?yyyy=2015&mm=08&dd=30, /2024/05/materia), mesmo que
            o <lastmod> seja recente: alguns sites marcam todo o arquivo histórico
            com a data do dia.
        Aceita sitemap compactado (.xml.gz).
        """
        corte = cfg.SITEMAP_CORTE            # 'AAAA-MM-DD'
        if urls.ano_antigo(response.url, corte):
            return  # sitemap de ano antigo que já estava na fila
        conteudo = response.content or b''
        if conteudo[:2] == b'\x1f\x8b':      # gzip
            try:
                import gzip
                conteudo = gzip.decompress(conteudo)
            except Exception:
                return
        try:
            raiz = lxml.etree.fromstring(conteudo)
        except Exception:
            return
        ns = '{http://www.sitemaps.org/schemas/sitemap/0.9}'

        def antigo(item, u):
            data = (item.findtext(ns + 'lastmod') or '').strip()[:10]
            if data and data < corte:          # texto AAAA-MM-DD ordena como data
                return True
            return urls.ano_antigo(u, corte)

        # índice de sitemaps -> outros sitemaps
        for item in raiz.iter(ns + 'sitemap'):
            u = (item.findtext(ns + 'loc') or '').strip()
            if not u or antigo(item, u):
                continue
            if urls.SITEMAP_NAO_MATERIA.search(u.rsplit('/', 1)[-1]):
                continue  # sitemap de tags/categorias/autores: não lista matérias
            # prioridade 3: abaixo das matérias do tema (4 a 6), para os
            # documentos começarem a entrar enquanto os sitemaps são lidos
            self.crawl(u, callback=self.sitemap, save=response.save,
                       priority=3, age=cfg.IDADE_HUB,
                       itag='corte-' + cfg.SITEMAP_CORTE)

        # sitemap de URLs -> páginas
        for item in raiz.iter(ns + 'url'):
            u = (item.findtext(ns + 'loc') or '').strip()
            if u and not antigo(item, u) and urls.TERMOS_SITEMAP.search(u):
                self.seguir(u, response.url, 2)

    # -- páginas HTML --------------------------------------------------------
    @config(priority=3)
    def pagina(self, response):
        tipo = response.headers.get('Content-Type', '')
        if 'pdf' in tipo:
            return self.pdf(response)
        if 'html' not in tipo and 'xml' not in tipo:
            return
        prof = response.save.get('prof', 0) if response.save else 0

        # 1) extrai links ANTES de limpar o HTML
        links = []
        for a in response.doc('a[href]').items():
            href = a.attr.href
            if href and not href.startswith(('javascript:', 'mailto:', 'tel:', '#')):
                links.append((href, a.text()[:200]))

        # 2) extrai texto e mede a relevância
        arvore = texto.parse_html(response.text, response.content)
        if arvore is None:
            return
        campos = texto.extrair_html(arvore, response.url)
        score, termos = texto.relevancia(campos['titulo'], campos['texto'], response.url)
        relevante = score >= cfg.LIMIAR_RELEVANCIA

        # 3) expande a fronteira: hubs (sementes/seções) sempre; demais só se relevantes
        if relevante or prof <= cfg.PROF_HUB:
            for href, ancora in links:
                self.seguir(urljoin(response.url, href), response.url, prof + 1, ancora)

        # 4) grava só o que é do tema e tem texto suficiente
        if not relevante or len(campos['texto']) < 300:
            return
        campos.update({
            'tipo': 'html',
            'fonte': urls.dominio(response.url),
            'url_final': response.url,
            'url_origem': (response.save or {}).get('origem'),
            'profundidade': prof,
            'status_http': response.status_code,
            'relevancia': score,
            'termos': termos,
            'hash_conteudo': texto.hash_conteudo(campos['texto']),
        })
        return campos

    # -- PDFs (planos de governo) -------------------------------------------
    @config(priority=7)
    def pdf(self, response):
        """Grava o PDF só se o texto das primeiras páginas for do tema."""
        conteudo = response.content or b''
        if not conteudo.startswith(b'%PDF'):
            return
        inicio = pdfs.texto_inicial(conteudo)
        score, termos = texto.relevancia(response.url.rsplit('/', 1)[-1], inicio, response.url)
        if score < cfg.LIMIAR_RELEVANCIA:
            return  # edital, balanço, relatório fora do tema, PDF escaneado sem texto
        reg = pdfs.salvar(response, DIR_PROJETO)
        if reg:
            reg.update({'relevancia': score, 'termos': termos, 'texto': inicio})
        return reg
