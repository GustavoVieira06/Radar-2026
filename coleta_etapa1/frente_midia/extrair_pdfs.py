#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Extrai o texto dos PDFs coletados (planos de governo).

Para cada PDF em dados/coleta/<projeto>/pdfs/ grava, ao lado, um .json com o
texto de cada página. Só processa o que ainda não foi extraído, então pode ser
rodado várias vezes durante a coleta.

    python extrair_pdfs.py
"""
from __future__ import print_function, unicode_literals

import io
import json
import os
import sys

RAIZ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, RAIZ)

from riw import config as cfg  # noqa: E402
from riw.pdfs import extrair_texto  # noqa: E402


def main():
    total = paginas_total = erros = 0
    if not os.path.isdir(cfg.DIR_COLETA):
        print('nenhuma coleta ainda')
        return
    for projeto in sorted(os.listdir(cfg.DIR_COLETA)):
        dir_pdfs = os.path.join(cfg.DIR_COLETA, projeto, 'pdfs')
        if not os.path.isdir(dir_pdfs):
            continue
        for nome in sorted(os.listdir(dir_pdfs)):
            if not nome.endswith('.pdf'):
                continue
            destino = os.path.join(dir_pdfs, nome[:-4] + '.json')
            if os.path.exists(destino):
                continue
            try:
                paginas = extrair_texto(os.path.join(dir_pdfs, nome))
            except Exception as e:  # PDF corrompido/criptografado
                erros += 1
                print('erro em %s/%s: %s' % (projeto, nome, e))
                continue
            with io.open(destino, 'w', encoding='utf-8') as fp:
                json.dump({'arquivo_pdf': 'pdfs/' + nome, 'paginas': paginas}, fp,
                          ensure_ascii=False)
            total += 1
            paginas_total += len(paginas)
    print('PDFs extraídos: %d (%d páginas), erros: %d' % (total, paginas_total, erros))


if __name__ == '__main__':
    main()
