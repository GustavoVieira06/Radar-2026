# -*- coding: utf-8 -*-
"""
Compressão das listas de postings: d-gaps + Variable Byte (VByte).

Cada posting é o par (unidade, tf). Em vez de gravar o número da unidade,
grava-se a diferença para a anterior (d-gap), que é pequena para termos
frequentes. Cada inteiro usa 7 bits por byte; o bit mais alto indica que o
número continua no byte seguinte. Inteiros < 128 ocupam 1 byte.
"""


def vbyte(n, out):
    while n >= 128:
        out.append((n & 127) | 128)
        n >>= 7
    out.append(n)


def vbyte_bytes(n):
    out = bytearray()
    vbyte(n, out)
    return bytes(out)


def ler_vbyte(buf, pos):
    """Lê um inteiro de buf a partir de pos. Devolve (valor, nova_pos)."""
    n = 0
    shift = 0
    while True:
        b = buf[pos]
        pos += 1
        n |= (b & 127) << shift
        if b < 128:
            return n, pos
        shift += 7


def decodificar_postings(buf):
    """bytes -> lista de (unidade, tf), desfazendo os d-gaps."""
    res = []
    pos = 0
    fim = len(buf)
    atual = 0
    while pos < fim:
        gap, pos = ler_vbyte(buf, pos)
        tf, pos = ler_vbyte(buf, pos)
        atual += gap
        res.append((atual, tf))
    return res


def decodificar_posicoes(buf, postings, unidades=None):
    """Posições de um termo -> {unidade: [posições]}.

    buf traz, para cada posting (na mesma ordem de `postings`), tf posições em
    d-gaps (a 1ª absoluta). Se `unidades` for dado, só essas são devolvidas
    (as demais são puladas, mas ainda precisam ser lidas byte a byte)."""
    res = {}
    pos = 0
    for u, tf in postings:
        if unidades is not None and u not in unidades:
            for _ in range(tf):                  # pula os inteiros desta posting
                while buf[pos] >= 128:
                    pos += 1
                pos += 1
            continue
        lista = []
        atual = 0
        for _ in range(tf):
            g, pos = ler_vbyte(buf, pos)
            atual += g
            lista.append(atual)
        res[u] = lista
    return res
