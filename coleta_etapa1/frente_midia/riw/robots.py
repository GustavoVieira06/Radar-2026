# -*- coding: utf-8 -*-
"""
Correção da checagem de robots.txt do fetcher do pyspider.

O código original (TornadoFetcher.can_fetch) quebra com AttributeError quando o
download do robots.txt dá erro HTTP, inclusive no caso comum de o site não ter
robots.txt (404). O resultado é que TODAS as URLs daquele site falham.

Regras aplicadas aqui (padrão do protocolo de exclusão de robôs, RFC 9309):
  - 2xx         -> usa as regras do arquivo
  - 4xx         -> não há restrições (o site não publicou regras)
  - 5xx / rede  -> por prudência, NÃO coleta o site por enquanto; tenta ler o
                   robots.txt de novo em 10 minutos (as URLs voltam pela
                   política de novas tentativas do pyspider)
"""
from __future__ import unicode_literals

import logging
import time

logger = logging.getLogger('fetcher')

ESPERA_APOS_ERRO = 10 * 60


def aplicar_correcao():
    import tornado.httpclient
    from tornado import gen
    from six.moves.urllib.parse import urljoin, urlsplit
    from six.moves.urllib.robotparser import RobotFileParser
    from pyspider.fetcher.tornado_fetcher import Fetcher

    @gen.coroutine
    def can_fetch(self, user_agent, url):
        dominio = urlsplit(url).netloc
        robot_txt = self.robots_txt_cache.get(dominio)
        if robot_txt is not None and time.time() - robot_txt.mtime() > self.robot_txt_age:
            robot_txt = None

        if robot_txt is None:
            robot_txt = RobotFileParser()
            linhas = []
            validade = None
            try:
                resposta = yield gen.maybe_future(self.http_client.fetch(
                    urljoin(url, '/robots.txt'), connect_timeout=10, request_timeout=30,
                    user_agent=user_agent))
                linhas = (resposta.body or b'').decode('utf-8', 'ignore').splitlines()
            except tornado.httpclient.HTTPError as e:
                if 400 <= e.code < 500:
                    linhas = []  # sem robots.txt: tudo liberado
                else:
                    logger.warning('robots.txt de %s indisponível (%s): pausando o site por %d min',
                                   dominio, e, ESPERA_APOS_ERRO // 60)
                    linhas = ['User-agent: *', 'Disallow: /']
                    validade = ESPERA_APOS_ERRO
            except Exception as e:  # erro de rede fora do tornado
                logger.warning('robots.txt de %s falhou (%r): pausando o site', dominio, e)
                linhas = ['User-agent: *', 'Disallow: /']
                validade = ESPERA_APOS_ERRO

            robot_txt.parse(linhas)
            robot_txt.modified()
            if validade is not None:
                # faz o cache "vencer" daqui a `validade` segundos
                robot_txt.last_checked = time.time() - self.robot_txt_age + validade
            self.robots_txt_cache[dominio] = robot_txt

        raise gen.Return(robot_txt.can_fetch(user_agent, url))

    Fetcher.can_fetch = can_fetch
