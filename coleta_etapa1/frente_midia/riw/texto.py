# -*- coding: utf-8 -*-
"""Extração de texto de HTML e cálculo de relevância para o tema."""
from __future__ import unicode_literals

import hashlib
import re
import unicodedata

# 'form' NÃO entra: alguns portais (ASP.NET) envolvem a página inteira num <form>
REMOVER = ('script', 'style', 'noscript', 'iframe', 'svg', 'nav', 'footer',
           'header', 'aside', 'button', 'select', 'template')

# termos que indicam PROMESSA / PROPOSTA (peso 3)
TERMOS_FORTES = [
    'plano de governo', 'programa de governo', 'proposta de governo', 'propostas',
    'proposta', 'promessa', 'promessas', 'promete', 'prometeu', 'prometem', 'propoe',
    'propos', 'compromisso de campanha', 'se eleito', 'se eleita', 'vai criar',
    'plataforma de governo', 'diretrizes de governo',
]
# termos de CONTEXTO ELEITORAL (peso 1)
TERMOS_CONTEXTO = [
    'eleicao', 'eleicoes', 'eleitoral', 'candidato', 'candidata', 'candidatos',
    'candidatura', 'campanha', 'governador', 'governadora', 'presidente',
    'presidencia', 'senador', 'senadora', 'deputado', 'deputada', 'prefeito',
    'debate', 'sabatina', 'tse', 'primeiro turno', 'segundo turno', '1o turno',
    '2o turno', 'urna', 'eleitor', 'eleitores', 'horario eleitoral', 'coligacao',
    'federacao', 'vice',
]

_ESPACOS = re.compile(r'\s+')


def sem_acento(texto):
    texto = unicodedata.normalize('NFKD', texto)
    texto = ''.join(c for c in texto if not unicodedata.combining(c))
    return texto.replace('º', 'o').replace('ª', 'a').lower()


def _padrao(termos):
    return re.compile(r'\b(' + '|'.join(re.escape(t) for t in termos) + r')\b')


_RE_FORTES = _padrao(TERMOS_FORTES)
_RE_CONTEXTO = _padrao(TERMOS_CONTEXTO)


def relevancia(titulo, texto, url=''):
    """Pontuação simples por presença de termos (coletor focado).

    score = 3 * (termos de promessa distintos) + (termos de contexto distintos)
    Termos no título valem em dobro. Exige ao menos um termo de contexto.
    """
    corpo = sem_acento((texto or '')[:20000])
    tit = sem_acento(titulo or '')
    fortes = set(_RE_FORTES.findall(corpo))
    contexto = set(_RE_CONTEXTO.findall(corpo))
    fortes_t = set(_RE_FORTES.findall(tit))
    contexto_t = set(_RE_CONTEXTO.findall(tit))
    score = 3 * len(fortes) + len(contexto) + 3 * len(fortes_t) + len(contexto_t)
    if re.search(r'eleic|plano-de-governo|propost', url or '', re.I):
        score += 2
    if not (contexto or contexto_t):
        score = min(score, 1)
    return score, sorted(fortes | fortes_t | contexto | contexto_t)


def parse_html(texto_unicode, conteudo_bytes=None):
    """Monta a árvore lxml do HTML.

    Usa o texto já decodificado pelo pyspider; se a página tiver declaração
    de encoding (<?xml ... encoding=...?>), o lxml recusa texto unicode, então
    cai para os bytes crus (e o lxml lê o encoding da própria página).
    """
    import lxml.html
    for entrada in (texto_unicode, conteudo_bytes):
        if not entrada:
            continue
        try:
            return lxml.html.fromstring(entrada)
        except Exception:
            continue
    return None


def _meta(doc, *nomes):
    for nome in nomes:
        for attr in ('property', 'name', 'itemprop'):
            r = doc.xpath('//meta[@%s="%s"]/@content' % (attr, nome))
            if r and r[0].strip():
                return r[0].strip()
    return ''


def extrair_html(doc, url=''):
    """Recebe o lxml etree da página (response.etree) e devolve os campos."""
    titulo = _meta(doc, 'og:title') or ' '.join(doc.xpath('//title//text()')).strip()
    descricao = _meta(doc, 'og:description', 'description')
    data_pub = _meta(doc, 'article:published_time', 'datePublished', 'date',
                     'publish-date', 'DC.date.issued')
    autor = _meta(doc, 'author', 'article:author')
    canonico = doc.xpath('//link[@rel="canonical"]/@href')
    idioma = (doc.xpath('/html/@lang') or [''])[0]

    # 1) texto completo nos dados estruturados (JSON-LD "articleBody"), comum
    #    em portais de notícia; é a fonte mais limpa quando existe
    texto = _corpo_json_ld(doc)

    for el in doc.xpath('//' + ' | //'.join(REMOVER)):
        pai = el.getparent()
        if pai is not None:
            pai.remove(el)

    # 2) senão, o bloco da página que concentra mais parágrafos
    if len(texto) < 300:
        texto_blocos = _texto_por_densidade(doc)
        if len(texto_blocos) > len(texto):
            texto = texto_blocos

    return {
        'titulo': _ESPACOS.sub(' ', titulo)[:500],
        'descricao': descricao[:1000],
        'data_publicacao': data_pub[:40],
        'autor': autor[:200],
        'url_canonica': canonico[0].strip() if canonico else '',
        'idioma': idioma[:10],
        'texto': texto,
    }


def _limpo(el):
    return _ESPACOS.sub(' ', el.text_content() or '').strip()


def _corpo_json_ld(doc):
    """Maior "articleBody" encontrado nos blocos <script type="application/ld+json">."""
    import json
    corpos = []

    def varrer(obj):
        if isinstance(obj, dict):
            corpo = obj.get('articleBody')
            if isinstance(corpo, str):
                corpos.append(corpo)
            for v in obj.values():
                varrer(v)
        elif isinstance(obj, list):
            for v in obj:
                varrer(v)

    for bruto in doc.xpath('//script[@type="application/ld+json"]/text()'):
        try:
            varrer(json.loads(bruto, strict=False))
        except ValueError:
            continue
    if not corpos:
        return ''
    melhor = max(corpos, key=len)
    # articleBody às vezes vem com HTML dentro
    melhor = re.sub(r'<[^>]+>', ' ', melhor)
    return '\n'.join(_ESPACOS.sub(' ', l).strip() for l in melhor.splitlines() if l.strip())


def _texto_por_densidade(doc):
    """Acha o contêiner com mais texto em parágrafos (estilo "readability").

    Cada parágrafo longo soma pontos para o elemento pai (peso 1) e para o avô
    (peso 0,5); o elemento com mais pontos é o corpo da matéria, esteja ele num
    <article>, num <div class="entry-content"> ou em qualquer outro lugar.
    """
    pontos = {}
    total = 0
    for p in doc.iter('p'):
        n = len(_limpo(p))
        if n < 40:
            continue
        total += n
        pai = p.getparent()
        if pai is not None:
            pontos[pai] = pontos.get(pai, 0) + n
            avo = pai.getparent()
            if avo is not None:
                pontos[avo] = pontos.get(avo, 0) + n / 2.0
    if pontos:
        alvo = max(pontos, key=pontos.get)
        blocos = [_limpo(el) for el in alvo.iter('p', 'h2', 'h3', 'h4', 'li', 'blockquote')]
        texto = '\n'.join(b for b in blocos if len(b) > 1)
        if len(texto) >= 0.4 * total:
            return texto
        # parágrafos espalhados em vários blocos: junta todos os longos
        return '\n'.join(t for t in (_limpo(p) for p in doc.iter('p')) if len(t) >= 40)
    # página sem <p> (texto em <div>/<br>): usa article/main/body
    for xp in ('//article', '//main', '//*[@role="main"]', '//body'):
        achados = doc.xpath(xp)
        if achados:
            return _limpo(max(achados, key=lambda e: len(e.text_content() or '')))
    return _limpo(doc)


def hash_conteudo(texto):
    """Impressão digital do texto normalizado, para detectar duplicatas exatas."""
    base = _ESPACOS.sub(' ', sem_acento(texto or '')).strip()
    return hashlib.sha1(base.encode('utf-8')).hexdigest()
