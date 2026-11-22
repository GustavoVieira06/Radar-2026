# -*- coding: utf-8 -*-
"""Normalização e filtros de URL (política de seleção)."""
from __future__ import unicode_literals

import re

try:
    from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
except ImportError:  # Python 2
    from urlparse import urlsplit, urlunsplit, parse_qsl
    from urllib import urlencode

# parâmetros de rastreamento que geram URLs diferentes para a mesma página
PARAMS_IGNORADOS = re.compile(
    r'^(utm_\w+|fbclid|gclid|mc_cid|mc_eid|ref|ref_src|cmpid|origin|from|'
    r'share|amp|outputType|_ga|__twitter_impression|replytocom|channel|source|'
    r'url-retorno|url_retorno|redirect|returnurl|origem)$', re.I)

EXT_IGNORADAS = re.compile(
    r'\.(jpe?g|png|gif|webp|svg|ico|bmp|tiff?|mp3|mp4|m4a|avi|mov|wmv|webm|ogg|'
    r'zip|rar|7z|gz|tar|exe|dmg|apk|css|js|json|xml|rss|woff2?|ttf|eot|'
    r'docx?|xlsx?|pptx?|csv)$', re.I)

# seções que não têm a ver com o tema ou que são armadilhas de coleta
CAMINHOS_IGNORADOS = re.compile(
    r'/(login|entrar|cadastro|assine|assinatura|newsletter|busca|search|'
    r'esporte|esportes|futebol|horoscopo|receitas|novelas|celebridades|bbb|'
    r'loterias|classificados|imoveis|carros|games|cotacoes|previsao-do-tempo|'
    r'podcasts?|videos?|fotos|galerias?|ao-vivo|tag/[^/]*(futebol|bbb)|'
    r'wp-admin|wp-login|feed|print|imprimir|comentarios|share|compartilhar|'
    r'ofertas|assine-ja|paywall|minha-conta|cadastre-se|clube|loja)(/|$|\?)',
    re.I)

TERMOS_URL = re.compile(
    r'eleic|eleito|candidat|plano-de-governo|programa-de-governo|propost|promess|'
    r'promete|governador|presidencia|presidente|senado|debate|sabatina|campanha|'
    r'tse|urna|pesquisa-eleitoral', re.I)

# filtro MAIS RESTRITO para URLs vindas de sitemap: sem termos ambíguos como
# "presidente" (de clube, de empresa), "debate", "campanha" (publicitária)
TERMOS_SITEMAP = re.compile(
    r'eleic|eleit|candidat|plano-de-governo|programa-de-governo|propost|promess|'
    r'promete|governador|sabatina|pesquisa-eleitoral|urna|'
    # seção de política: muitas matérias sobre candidatos e propostas não têm
    # termo eleitoral no endereço (ex.: /politica/noticia/2026/09/tarcisio-...);
    # entram na fila e o filtro de relevância do TEXTO decide se são gravadas
    r'/politica|/poder/', re.I)

# links de PDF que valem a pena baixar (URL ou texto do link, sem acento)
PDF_TEMA = re.compile(
    r'plano|governo|propost|programa|candidat|eleic|eleit|tse|campanha|promess|'
    r'diretriz|compromisso', re.I)

# seção (1º nível do caminho) fora do tema, ex.: /poder-sportsmkt/..., /esportes/...
SECAO_FORA = re.compile(
    r'sport|esporte|futebol|entretenimento|celebridade|novela|horoscopo|receita|'
    r'turismo|gastronomia|games|tecnologia|saude-e-bem-estar|pop-arte|cultura|'
    r'apostas|loteria|divirta|euestudante|concursos?|vestibular|enem|classificados|'
    r'horoscopo|quem-somos|fale-conosco|expediente|anuncie|trabalhe-conosco|'
    r'politica-de-privacidade|termos-de-uso|assinaturas?|newsletters?', re.I)

# sitemaps que listam páginas de tag/categoria/autor, não matérias
SITEMAP_NAO_MATERIA = re.compile(
    r'tag|categor|author|autor|page-sitemap|/pages?-|topic|editoria|colunista', re.I)


# ano "solto" no endereço: /2024/, -2024-, =2024 (não pega id123 2024 colado em letras)
_ANO = re.compile(r'(?<![0-9A-Za-z])((?:19|20)[0-9]{2})(?![0-9A-Za-z])')


def ano_antigo(url, corte):
    """True se o endereço traz um ano e o mais recente deles é anterior ao corte.

    Ex. (corte 2026-01-01): sitemap.xml?yyyy=2015&mm=08 -> True;
    /2024/05/materia -> True; /2026/09/materia -> False; sem ano -> False.
    Só olha o caminho e a consulta, não o domínio.
    """
    partes = urlsplit(url)
    anos = [int(a) for a in _ANO.findall(partes.path + '?' + partes.query) if int(a) <= 2030]
    return bool(anos) and max(anos) < int(corte[:4])


def secao_fora_do_tema(url):
    partes = [p for p in urlsplit(url).path.split('/') if p]
    return bool(partes) and bool(SECAO_FORA.search(partes[0]))


def dominio(url):
    host = (urlsplit(url).hostname or '').lower()
    return host[4:] if host.startswith('www.') else host


def normalizar(url):
    """Forma canônica usada como identificador (o taskid é o md5 dela)."""
    partes = urlsplit(url.strip())
    esquema = partes.scheme.lower()
    if esquema not in ('http', 'https'):
        return None
    host = (partes.hostname or '').lower()
    if not host:
        return None
    porta = partes.port
    netloc = host if porta in (None, 80, 443) else '%s:%d' % (host, porta)
    caminho = re.sub(r'/{2,}', '/', partes.path or '/')
    consulta = [(k, v) for k, v in parse_qsl(partes.query, keep_blank_values=True)
                if not PARAMS_IGNORADOS.match(k)]
    consulta.sort()
    return urlunsplit((esquema, netloc, caminho, urlencode(consulta), ''))


def permitido(url, dominios):
    """True se a URL pertence a um domínio permitido e não cai nos filtros."""
    dom = dominio(url)
    if not any(dom == d or dom.endswith('.' + d) for d in dominios):
        return False
    caminho = urlsplit(url).path
    if EXT_IGNORADAS.search(caminho):
        return False
    if CAMINHOS_IGNORADOS.search(caminho):
        return False
    if secao_fora_do_tema(url):
        return False
    if len(url) > 400 or caminho.count('/') > 12:
        return False  # armadilhas (calendários infinitos, URLs geradas)
    return True


def eh_pdf(url):
    return urlsplit(url).path.lower().endswith('.pdf')


def prioridade(url, texto_ancora=''):
    """0..9: maior = coletado antes."""
    if eh_pdf(url):
        return 7
    alvo = url + ' ' + (texto_ancora or '')
    acertos = len(set(m.group(0).lower() for m in TERMOS_URL.finditer(alvo)))
    return min(6, 2 + acertos)
