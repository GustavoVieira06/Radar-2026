# -*- coding: utf-8 -*-
"""Gravação de PDFs coletados e extração do texto deles."""
from __future__ import unicode_literals

import hashlib
import io
import os

from . import urls

MAX_BYTES_PDF = 30 * 1024 * 1024


def salvar(response, dir_projeto, extras=None):
    """Grava o PDF da resposta em <dir_projeto>/pdfs e devolve o registro.

    Retorna None se a resposta não for um PDF válido ou for grande demais.
    """
    conteudo = response.content or b''
    if not conteudo.startswith(b'%PDF') or len(conteudo) > MAX_BYTES_PDF:
        return None
    dir_pdfs = os.path.join(dir_projeto, 'pdfs')
    if not os.path.isdir(dir_pdfs):
        os.makedirs(dir_pdfs)
    nome = hashlib.md5(response.url.encode('utf-8')).hexdigest() + '.pdf'
    with io.open(os.path.join(dir_pdfs, nome), 'wb') as fp:
        fp.write(conteudo)
    save = response.save or {}
    reg = {
        'tipo': 'pdf',
        'fonte': urls.dominio(response.url),
        'url_final': response.url,
        'url_origem': save.get('origem'),
        'profundidade': save.get('prof', 0),
        'status_http': response.status_code,
        'arquivo_pdf': 'pdfs/' + nome,
        'bytes': len(conteudo),
        'titulo': os.path.basename(response.url.split('?')[0]),
        'texto': '',  # preenchido por extrair_pdfs.py
        'hash_conteudo': hashlib.sha1(conteudo).hexdigest(),
    }
    if extras:
        reg.update(extras)
    return reg


def extrair_texto(caminho):
    """Texto de todas as páginas do PDF (requer `pypdf`)."""
    from pypdf import PdfReader
    leitor = PdfReader(caminho)
    paginas = []
    for pagina in leitor.pages:
        try:
            paginas.append(pagina.extract_text() or '')
        except Exception:  # página corrompida não derruba o documento
            paginas.append('')
    return paginas


def texto_inicial(conteudo, max_paginas=5):
    """Texto das primeiras páginas de um PDF em memória ('' se não der para ler)."""
    try:
        from pypdf import PdfReader
        leitor = PdfReader(io.BytesIO(conteudo))
        partes = []
        for pagina in leitor.pages[:max_paginas]:
            try:
                partes.append(pagina.extract_text() or '')
            except Exception:
                continue
        return '\n'.join(partes)
    except Exception:
        return ''
