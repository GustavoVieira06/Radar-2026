# -*- coding: utf-8 -*-
"""
Pré-processamento do texto: limpeza, análise léxica, stopwords e stemming.

Ordem aplicada a cada documento:
  1. limpeza    - tira linhas de "ruído" que sobraram da extração (leia também,
                  publicidade, créditos de foto, chamadas para redes sociais...),
                  linhas repetidas, URLs e e-mails;
  2. análise léxica (tokenização) - minúsculas, separação em palavras e números
                  (palavras com hífen ficam juntas: "pré-candidato"; números
                  "1.000" -> "1000"; descarta letras soltas e tokens gigantes);
  3. stopwords  - remove palavras funcionais (opcional);
  4. stemming   - reduz ao radical com o Snowball para português (opcional);
  5. normalização final - remove acentos do termo ("saúde" -> "saude").
"""
from __future__ import unicode_literals

import re
import unicodedata

# ---------------------------------------------------------------------------
# 1. limpeza
# ---------------------------------------------------------------------------
_RUIDO = re.compile(
    r'^\s*(leia (tamb[eé]m|mais)|veja (tamb[eé]m|mais)|saiba mais|publicidade|'
    r'continua (ap[oó]s|depois d)|siga (o|a|nos)\b|inscreva-se|assine\b|compartilh|'
    r'receba (as|nossas) not[ií]cias|clique aqui|participe d[oe] (canal|grupo)|'
    r'(foto|imagem|cr[eé]dito|reprodu[cç][aã]o)\s*[:/(-]|v[ií]deo:|ou[cç]a\b|'
    r'baixe o app|todos os direitos|copyright|©|whatsapp|telegram|newsletter)',
    re.I)
_URL = re.compile(r'(https?://|www\.)\S+|\S+@\S+\.\S+', re.I)


def limpar(texto):
    """Remove ruído do texto extraído. Devolve (texto_limpo, chars_removidos)."""
    if not texto:
        return '', 0
    vistas = set()
    saida = []
    for linha in texto.split('\n'):
        l = linha.strip()
        if not l or _RUIDO.match(l):
            continue
        chave = l.lower()
        if chave in vistas:          # linha repetida (menu, rodapé, legenda)
            continue
        vistas.add(chave)
        saida.append(l)
    limpo = _URL.sub(' ', '\n'.join(saida))
    return limpo, len(texto) - len(limpo)


# ---------------------------------------------------------------------------
# 2. análise léxica
# ---------------------------------------------------------------------------
_TOKEN = re.compile(r"\d+(?:[.,]\d+)*|[^\W\d_]+(?:-[^\W\d_]+)*", re.U)
MAX_TOKEN = 30


def tokenizar(texto):
    """Minúsculas + tokens. Mantém acentos (o stemmer usa); remove depois."""
    tokens = []
    for t in _TOKEN.findall(texto.lower()):
        if t[0].isdigit():
            t = t.replace('.', '').replace(',', '')
            if len(t) > 8:            # telefones, códigos, CPFs...
                continue
        elif len(t) < 2 or len(t) > MAX_TOKEN:
            continue
        tokens.append(t)
    return tokens


def sem_acento(s):
    return ''.join(c for c in unicodedata.normalize('NFKD', s)
                   if not unicodedata.combining(c))


# ---------------------------------------------------------------------------
# 3. stopwords (lista do Snowball/NLTK para português + complementos da coleta)
# ---------------------------------------------------------------------------
_STOP = """
a à ao aos aquela aquelas aquele aqueles aquilo as às até com como da das de dela delas
dele deles depois do dos e é ela elas ele eles em entre era eram éramos essa essas esse
esses esta está estamos estão estar estas estava estavam estávamos este esteja estejam
estejamos estes esteve estive estivemos estiver estivera estiveram estivéramos estiverem
estivermos estivesse estivessem estivéssemos estou eu foi fomos for fora foram fôramos
forem formos fosse fossem fôssemos fui há haja hajam hajamos hão havemos haver hei houve
houvemos houver houvera houverá houveram houvéramos houverão houverei houverem houveremos
houveria houveriam houveríamos houvermos houvesse houvessem houvéssemos isso isto já lhe
lhes mais mas me mesmo meu meus minha minhas muito na não nas nem no nos nós nossa nossas
nosso nossos num numa o os ou para pela pelas pelo pelos por qual quando que quem são se
seja sejam sejamos sem ser será serão serei seremos seria seriam seríamos seu seus só
somos sou sua suas também te tem tém temos tenha tenham tenhamos tenho terá terão terei
teremos teria teriam teríamos teu teus teve tinha tinham tínhamos tive tivemos tiver
tivera tiveram tivéramos tiverem tivermos tivesse tivessem tivéssemos tu tua tuas um uma
umas uns você vocês vos
ainda além assim bem cada coisa contra disse diz dizer dois duas durante enquanto então
essa etc fazer faz feito ficar fica foi hoje lá onde outra outras outro outros pode podem
porque porém pois quase sobre segundo sendo seguinte sempre sido tão tanto toda todas todo
todos três vai vão ver vez vezes via
"""
STOPWORDS = frozenset(sem_acento(w) for w in _STOP.split())


# ---------------------------------------------------------------------------
# 4-5. normalizador de termos (com cache: cada palavra distinta é processada 1 vez)
# ---------------------------------------------------------------------------
class Normalizador(object):
    """Converte um token da análise léxica no termo do índice (ou None)."""

    def __init__(self, stopwords=True, stemming=True):
        self.stopwords = stopwords
        self.stemming = stemming
        self.cache = {}
        self._stem = None
        if stemming:
            try:
                import snowballstemmer
            except ImportError:
                raise SystemExit('Falta o stemmer: pip install snowballstemmer')
            self._stem = snowballstemmer.stemmer('portuguese').stemWord

    def termo(self, token):
        try:
            return self.cache[token]
        except KeyError:
            pass
        base = sem_acento(token)
        if self.stopwords and base in STOPWORDS:
            t = None
        elif self._stem is not None and not token[0].isdigit():
            t = sem_acento(self._stem(token)) or base
        else:
            t = base
        self.cache[token] = t
        return t

    def termos(self, tokens):
        c = self.cache
        f = self.termo
        out = []
        for tok in tokens:
            t = c[tok] if tok in c else f(tok)
            if t is not None:
                out.append(t)
        return out

    def termos_com_posicoes(self, tokens):
        """Como termos(), mas devolve também a posição de cada termo no texto
        original (índice do token, contando as stopwords removidas)."""
        c = self.cache
        f = self.termo
        out, pos = [], []
        for i, tok in enumerate(tokens):
            t = c[tok] if tok in c else f(tok)
            if t is not None:
                out.append(t)
                pos.append(i)
        return out, pos

    def descricao(self):
        return '%s / %s' % ('com stopwords removidas' if self.stopwords else 'sem remover stopwords',
                            'com stemming' if self.stemming else 'sem stemming')
