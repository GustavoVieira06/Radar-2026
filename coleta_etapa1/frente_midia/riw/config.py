# -*- coding: utf-8 -*-
"""Parâmetros do coletor. Os valores podem ser trocados por variáveis de ambiente."""
from __future__ import unicode_literals

import io
import os

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Pastas ------------------------------------------------------------------
DIR_DADOS = os.path.abspath(os.environ.get('RIW_DADOS', os.path.join(RAIZ, 'dados')))
DIR_CONFIG = os.path.abspath(os.environ.get('RIW_CONFIG', os.path.join(RAIZ, 'config')))
DIR_ESTADO = os.path.join(DIR_DADOS, 'estado')     # taskdb (fila)
DIR_COLETA = os.path.join(DIR_DADOS, 'coleta')     # resultdb (documentos)

# Identificação (política de cortesia) ------------------------------------
CONTATO = os.environ.get('RIW_CONTATO', 'contato-do-grupo@exemplo.com')
USER_AGENT = os.environ.get(
    'RIW_USER_AGENT',
    'RIW-Promessas/1.0 (coletor academico; +mailto:%s)' % CONTATO)

# Critério de parada ------------------------------------------------------
META_DOCUMENTOS = int(os.environ.get('RIW_META', '60000'))
MAX_PROFUNDIDADE = int(os.environ.get('RIW_PROFUNDIDADE', '4'))

# Política de seleção (coletor focado) ------------------------------------
LIMIAR_RELEVANCIA = int(os.environ.get('RIW_LIMIAR', '4'))
# páginas com profundidade <= PROF_HUB têm os links expandidos mesmo que não
# sejam relevantes. 0 = só as sementes (capas das seções de eleições); a partir
# daí, só páginas relevantes têm os links seguidos (evita "vazar" pelo menu do site)
PROF_HUB = 0
# filtro temporal dos sitemaps: ignora matérias com <lastmod> anterior a esta data
SITEMAP_CORTE = os.environ.get('RIW_CORTE', '2026-01-01')

# Política de revisita (segundos) -----------------------------------------
IDADE_HUB = 6 * 3600          # capas/seções mudam várias vezes ao dia
IDADE_ARTIGO = 30 * 24 * 3600  # notícia publicada quase não muda
IDADE_PDF = 30 * 24 * 3600

# Tolerância a falhas -----------------------------------------------------
TENTATIVAS = 3
TIMEOUT_CONEXAO = 20
TIMEOUT_TOTAL = 40   # site lento/bloqueando não prende conexão por muito tempo


def _ler_lista(nome):
    caminho = os.path.join(DIR_CONFIG, nome)
    itens = []
    if not os.path.exists(caminho):
        return itens
    with io.open(caminho, encoding='utf-8') as fp:
        for linha in fp:
            linha = linha.split('#', 1)[0].strip()
            if linha:
                itens.append(linha)
    return itens


def sementes():
    return _ler_lista('sementes.txt')


def sitemaps():
    """Domínios cujo robots.txt/sitemap será usado como fonte extra de URLs."""
    return _ler_lista('sitemaps.txt')


def dominios_permitidos():
    from .urls import dominio
    doms = set(d.lower().lstrip('.') for d in _ler_lista('dominios.txt'))
    for s in sementes() + sitemaps():
        doms.add(dominio(s if '://' in s else 'https://' + s))
    return doms
