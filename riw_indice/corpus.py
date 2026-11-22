# -*- coding: utf-8 -*-
"""
Leitura do corpus coletado na Etapa 1 e geração da "coleção tokenizada".

Corpus de entrada (gerado pelo coletor):
    dados/coleta/promessas/manifesto.tsv          1 linha por gravação (a última vale)
    dados/coleta/promessas/paginas/lote-NNNNN.jsonl[.gz]

Saída (passo "preparar", feito uma única vez):
    dados/indice/colecao/documentos.tsv   docid, id, url, titulo, fonte, ano, tipo
    dados/indice/colecao/tokens.jsonl.gz  1 linha por documento: lista de tokens
                                          (após limpeza + análise léxica)
    dados/indice/colecao/preparo.json     estatísticas e tempos da limpeza/tokenização

Como stopwords e stemming são aplicados palavra a palavra (com cache), todas as
configurações do índice partem desse mesmo arquivo de tokens: a parte cara
(ler o JSON e tokenizar 100 mil textos) é feita uma vez só.
"""
from __future__ import unicode_literals

import collections
import gzip
import io
import json
import os
import re
import time

from . import preproc

ANO_MIN, ANO_MAX = 1995, 2026
_ANO_URL = re.compile(r'(?<![0-9A-Za-z])((?:19|20)[0-9]{2})(?![0-9A-Za-z])')
_DATA_NUM = re.compile(r'(?<![0-9])([0-3]?[0-9])[/.-]([01]?[0-9])[/.-]((?:19|20)[0-9]{2})(?![0-9])')
_DATA_EXT = re.compile(r'(?<![0-9])[0-3]?[0-9] de (?:janeiro|fevereiro|mar[cç]o|abril|maio|junho|'
                       r'julho|agosto|setembro|outubro|novembro|dezembro) de ((?:19|20)[0-9]{2})', re.I)


def ano_do_documento(doc):
    """Ano de publicação: metadado do site -> ano na URL -> 1ª data do texto."""
    d = (doc.get('data_publicacao') or '').strip()
    if d[:4].isdigit() and ANO_MIN <= int(d[:4]) <= ANO_MAX:
        return d[:4]
    anos = [int(a) for a in _ANO_URL.findall(doc.get('url') or '') if ANO_MIN <= int(a) <= ANO_MAX]
    if anos:
        return str(max(anos))
    inicio = (doc.get('texto') or '')[:3000]
    achados = [(m.start(), m.group(3)) for m in _DATA_NUM.finditer(inicio)
               if 1 <= int(m.group(1)) <= 31 and 1 <= int(m.group(2)) <= 12]
    achados += [(m.start(), m.group(1)) for m in _DATA_EXT.finditer(inicio)]
    achados = [a for a in achados if ANO_MIN <= int(a[1]) <= ANO_MAX]
    return min(achados)[1] if achados else ''


def _abrir(caminho):
    if caminho.endswith('.gz'):
        return io.TextIOWrapper(gzip.open(caminho, 'rb'), encoding='utf-8')
    return io.open(caminho, 'r', encoding='utf-8')


def documentos(dir_colecao, limite_lotes=None):
    """Gera os documentos válidos (versão mais recente de cada id), na ordem dos lotes."""
    manifesto = os.path.join(dir_colecao, 'manifesto.tsv')
    dir_pag = os.path.join(dir_colecao, 'paginas')
    vivos = {}
    with io.open(manifesto, encoding='utf-8', errors='replace') as fp:
        cab = fp.readline().rstrip('\n').split('\t')
        i_id, i_lote, i_linha = cab.index('id'), cab.index('lote'), cab.index('linha')
        for ln in fp:
            c = ln.rstrip('\n').split('\t')
            if len(c) == len(cab):
                vivos[c[i_id]] = (c[i_lote].replace('.gz', ''), int(c[i_linha]))
    por_lote = collections.defaultdict(set)
    for lote, linha in vivos.values():
        por_lote[lote].add(linha)
    lotes = sorted(por_lote)
    if limite_lotes:
        lotes = lotes[:limite_lotes]
    for lote in lotes:
        caminho = os.path.join(dir_pag, lote)
        if not os.path.exists(caminho):
            caminho += '.gz'
        if not os.path.exists(caminho):
            continue
        linhas = por_lote[lote]
        with _abrir(caminho) as fp:
            for i, ln in enumerate(fp):
                if i in linhas:
                    try:
                        yield json.loads(ln)
                    except ValueError:
                        continue


def _url_host(url):
    from urllib.parse import urlsplit
    return urlsplit(url).netloc.lower()


def documentos_csv(caminho):
    """Gera os documentos do CSV unificado (corpus entregue na Etapa 1).

    Duas origens convivem no CSV:
      - coleta de mídia (pyspider + riw/filedb.py): colunas id, titulo, texto, fonte...
      - coleta institucional: coluna `result` com um dicionário Python
        {'url', 'title', 'text', 'domain'} (exportação padrão do pyspider).
    """
    import ast
    import csv
    import hashlib
    import sys
    lim = sys.maxsize
    while True:
        try:
            csv.field_size_limit(lim)
            break
        except OverflowError:   # Windows: o limite precisa caber num long de 32 bits
            lim = int(lim / 10)
    with io.open(caminho, encoding='utf-8-sig', newline='') as fp:
        for r in csv.DictReader(ln.replace('\0', '') for ln in fp):   # há bytes NUL no CSV
            if r.get('id'):
                doc = {k: r.get(k) or '' for k in ('id', 'url', 'titulo', 'texto', 'fonte', 'tipo',
                                                     'data_publicacao')}
                doc['origem'] = 'midia'
                yield doc
            elif r.get('result'):
                try:
                    d = ast.literal_eval(r['result'])
                except (ValueError, SyntaxError):
                    continue
                url = d.get('url') or r.get('url') or ''
                host = _url_host(url)
                yield {'id': hashlib.md5(url.encode('utf-8')).hexdigest(), 'url': url,
                       'titulo': d.get('title') or '', 'texto': d.get('text') or '',
                       'fonte': host[4:] if host.startswith('www.') else host, 'tipo': 'html',
                       'data_publicacao': '', 'origem': 'institucional'}


# páginas de listagem (paginação, tags, categorias, autores): só repetem trechos
# de outras matérias, então são retiradas na limpeza
_LISTAGEM = re.compile(r'/(page|pagina)/\d+|[?&](page|pagina|paged|p)=\d+|'
                       r'/(tag|tags|categoria|category|autor|author|editoria)/', re.I)


def eh_listagem(url):
    return bool(_LISTAGEM.search(url or ''))


def _tsv(s):
    return (s or '').replace('\t', ' ').replace('\n', ' ').replace('\r', ' ')


def preparar(dir_colecao, dir_saida, limite_lotes=None, log=print, csv_unificado=None):
    """Limpeza + análise léxica de todo o corpus -> documentos.tsv + tokens.jsonl.gz"""
    import hashlib
    if not os.path.isdir(dir_saida):
        os.makedirs(dir_saida)
    t0 = time.time()
    t_leitura = t_limpeza = t_token = 0.0
    n = chars_orig = chars_rem = n_tokens = 0
    por_ano = collections.Counter()
    vocab_bruto = set()
    heaps = []   # (tokens vistos, vocabulário) a cada 1000 docs - vocabulário SEM normalização
    lidos = 0
    descartes = collections.Counter()
    por_origem = collections.Counter()
    hashes = set()
    fonte_docs = documentos_csv(csv_unificado) if csv_unificado else documentos(dir_colecao, limite_lotes)
    tmp_tok = os.path.join(dir_saida, 'tokens.jsonl.gz.tmp')
    tmp_doc = os.path.join(dir_saida, 'documentos.tsv.tmp')
    textos = Textos(dir_saida, 'w')   # texto limpo de cada documento (trechos da busca)
    with gzip.open(tmp_tok, 'wt', encoding='utf-8', compresslevel=3) as ftok, \
            io.open(tmp_doc, 'w', encoding='utf-8') as fdoc:
        fdoc.write('docid\tid\turl\ttitulo\tfonte\tano\ttipo\torigem\tn_tokens\n')
        ta = time.time()
        for doc in fonte_docs:
            tb = time.time()
            t_leitura += tb - ta
            lidos += 1
            texto = doc.get('texto') or ''
            titulo = doc.get('titulo') or ''
            if eh_listagem(doc.get('url')):
                descartes['pagina_de_listagem'] += 1
                t_limpeza += time.time() - tb
                ta = time.time()
                continue
            h = hashlib.sha1(' '.join(texto.lower().split()).encode('utf-8')).digest()
            if h in hashes:
                descartes['texto_duplicado'] += 1
                t_limpeza += time.time() - tb
                ta = time.time()
                continue
            hashes.add(h)
            limpo, rem = preproc.limpar(texto)
            if len(limpo) < 200:
                descartes['texto_curto_apos_limpeza'] += 1
                t_limpeza += time.time() - tb
                ta = time.time()
                continue
            por_origem[doc.get('origem') or 'midia'] += 1
            tc = time.time()
            t_limpeza += tc - tb
            tokens = preproc.tokenizar(titulo + '\n' + limpo)
            td = time.time()
            t_token += td - tc
            ano = ano_do_documento(doc)
            por_ano[ano or 'sem data'] += 1
            fdoc.write('\t'.join([str(n), doc.get('id', ''), _tsv(doc.get('url')), _tsv(titulo)[:300],
                                  _tsv(doc.get('fonte')), ano, _tsv(doc.get('tipo')),
                                  doc.get('origem') or 'midia', str(len(tokens))]) + '\n')
            ftok.write(json.dumps(tokens, ensure_ascii=False, separators=(',', ':')) + '\n')
            textos.gravar(titulo + '\n' + limpo)
            chars_orig += len(texto)
            chars_rem += rem
            n_tokens += len(tokens)
            vocab_bruto.update(tokens)
            n += 1
            if n % 1000 == 0:
                heaps.append((n_tokens, len(vocab_bruto)))
                log('  preparados %d documentos (%.0f s)' % (n, time.time() - t0))
            ta = time.time()
    textos.fechar()
    os.replace(tmp_tok, os.path.join(dir_saida, 'tokens.jsonl.gz'))
    os.replace(tmp_doc, os.path.join(dir_saida, 'documentos.tsv'))
    heaps.append((n_tokens, len(vocab_bruto)))
    info = {
        'documentos_lidos': lidos,
        'documentos_descartados': dict(descartes),
        'documentos_por_origem': dict(por_origem),
        'documentos': n,
        'caracteres_originais': chars_orig,
        'caracteres_removidos_limpeza': chars_rem,
        'percentual_removido_limpeza': round(100.0 * chars_rem / max(chars_orig, 1), 2),
        'tokens': n_tokens,
        'tokens_por_documento': round(float(n_tokens) / max(n, 1), 1),
        'vocabulario_tokens_distintos': len(vocab_bruto),
        'tempo_total_s': round(time.time() - t0, 1),
        'tempo_leitura_json_s': round(t_leitura, 1),
        'tempo_limpeza_s': round(t_limpeza, 1),
        'tempo_tokenizacao_s': round(t_token, 1),
        'tamanho_tokens_gz_bytes': os.path.getsize(os.path.join(dir_saida, 'tokens.jsonl.gz')),
        'tamanho_textos_bytes': textos.tamanho(),
        'documentos_por_ano': dict(por_ano),
        'heaps_tokens_vocabulario': heaps,
    }
    with io.open(os.path.join(dir_saida, 'preparo.json'), 'w', encoding='utf-8') as fp:
        json.dump(info, fp, ensure_ascii=False, indent=1)
    return info


def tokens_por_documento(dir_preparo):
    with gzip.open(os.path.join(dir_preparo, 'tokens.jsonl.gz'), 'rt', encoding='utf-8') as fp:
        for ln in fp:
            yield json.loads(ln)


class Textos(object):
    """Texto limpo de cada documento, guardado em arquivo para montar os trechos
    (snippets) da busca. textos.bin = textos comprimidos com zlib, um após o
    outro; textos.idx = deslocamento de cada documento (inteiros de 64 bits).
    O documento docid é lido com um único seek, sem percorrer o arquivo."""

    def __init__(self, dir_colecao, modo='r'):
        import array
        self.bin = os.path.join(dir_colecao, 'textos.bin')
        self.idx = os.path.join(dir_colecao, 'textos.idx')
        self.desloc = array.array('q')
        if modo == 'w':
            self.fp = io.open(self.bin + '.tmp', 'wb')
            self.desloc.append(0)
        else:
            with io.open(self.idx, 'rb') as f:
                self.desloc.frombytes(f.read())
            self.fp = io.open(self.bin, 'rb')

    def gravar(self, texto):
        import zlib
        self.fp.write(zlib.compress(texto.encode('utf-8'), 6))
        self.desloc.append(self.fp.tell())

    def fechar(self):
        self.fp.close()
        if os.path.exists(self.bin + '.tmp'):
            with io.open(self.idx, 'wb') as f:
                f.write(self.desloc.tobytes())
            os.replace(self.bin + '.tmp', self.bin)

    def tamanho(self):
        return os.path.getsize(self.bin) + os.path.getsize(self.idx)

    def __len__(self):
        return len(self.desloc) - 1

    def ler(self, docid):
        import zlib
        a, b = self.desloc[docid], self.desloc[docid + 1]
        self.fp.seek(a)
        return zlib.decompress(self.fp.read(b - a)).decode('utf-8')
