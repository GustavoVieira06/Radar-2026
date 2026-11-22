# -*- coding: utf-8 -*-
"""
Cortesia POR SITE no fetcher do pyspider.

O pyspider limita só a taxa do projeto inteiro (`# rate:`). Como a fila é
ordenada por prioridade, ele pode mandar dezenas de pedidos seguidos para o
mesmo site (ex.: todas as matérias de um sitemap). O site reage reduzindo a
velocidade (as páginas passam a demorar 90 s e estouram o timeout), e essas
conexões lentas ocupam as vagas de download de todos os outros sites.

Esta correção faz, para cada site (host):
  - no máximo MAX_CONEXOES_POR_SITE downloads ao mesmo tempo;
  - no mínimo INTERVALO_POR_SITE segundos entre o início de dois pedidos.
Os pedidos que precisam esperar ficam numa fila de espera dentro do fetcher,
sem ocupar conexões, então os outros sites continuam sendo baixados.
"""
from __future__ import unicode_literals

import os
import time

MAX_CONEXOES_POR_SITE = 2
INTERVALO_POR_SITE = 0.5    # segundos
# Pedidos esperando a vez de um site ficam na memória do fetcher. O teto precisa
# ser alto: quando a fila do scheduler traz milhares de URLs seguidas do mesmo
# site (um sitemap inteiro), um teto baixo enche só com esse site e faz o fetcher
# parar de puxar tarefas, travando todos os outros sites.
MAX_EM_ESPERA = int(os.environ.get("RIW_MAX_ESPERA", "20000"))


def aplicar_correcao():
    from tornado import gen, locks
    from six.moves.urllib.parse import urlsplit
    from pyspider.fetcher.tornado_fetcher import Fetcher

    original_async_fetch = Fetcher.async_fetch
    original_init = Fetcher.__init__

    def __init__(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        cliente = self.http_client
        if not getattr(self, 'async_mode', True) or not hasattr(cliente, 'free_size'):
            return  # fetcher síncrono da WebUI (depuração): sem limite
        self._riw_sites = {}
        self._riw_esperando = 0
        livre_original = cliente.free_size

        def free_size():
            # com muita coisa esperando a vez, para de puxar tarefas do scheduler
            if self._riw_esperando >= MAX_EM_ESPERA:
                return 0
            return livre_original()
        cliente.free_size = free_size

    @gen.coroutine
    def async_fetch(self, task, callback=None):
        url = task.get('url') or 'data:,'
        if url.startswith('data:') or not hasattr(self, '_riw_sites'):
            resultado = yield original_async_fetch(self, task, callback)
            raise gen.Return(resultado)

        host = urlsplit(url).netloc.lower()
        site = self._riw_sites.get(host)
        if site is None:
            site = self._riw_sites[host] = {
                'semaforo': locks.Semaphore(MAX_CONEXOES_POR_SITE), 'proximo': 0.0}

        self._riw_esperando += 1
        try:
            yield site['semaforo'].acquire()
        finally:
            self._riw_esperando -= 1
        try:
            agora = time.time()
            inicio = max(agora, site['proximo'])
            site['proximo'] = inicio + INTERVALO_POR_SITE
            if inicio > agora:
                yield gen.sleep(inicio - agora)
            resultado = yield original_async_fetch(self, task, callback)
        finally:
            site['semaforo'].release()
        raise gen.Return(resultado)

    Fetcher.__init__ = __init__
    Fetcher.async_fetch = async_fetch
