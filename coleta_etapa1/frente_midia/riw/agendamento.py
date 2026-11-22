# -*- coding: utf-8 -*-
"""
Justiça entre sites no SCHEDULER do pyspider.

Problema: a fila do scheduler é ordenada só por prioridade. Quando um sitemap
traz milhares de URLs do mesmo site, o scheduler manda todas desse site em
sequência. Como o fetcher respeita o limite por site (riw/cortesia.py), essas
URLs ficavam esperando a vez dentro do fetcher. Passados 10 minutos
(TaskQueue.processing_timeout), o scheduler as considerava perdidas e as
reenviava ("processing: retry"); o resultado do download original era ignorado
("not processing pack") e a URL continuava na fila. Resultado: as mesmas
páginas baixadas várias vezes e a coleta travada.

Correção (no scheduler):
  - cada site tem no máximo MAX_EM_ANDAMENTO_POR_SITE URLs enviadas ao fetcher
    e ainda não concluídas;
  - as URLs excedentes vão para uma espera própria do site (na memória do
    scheduler, fora do controle de prazo do pyspider) e são enviadas assim
    que uma das URLs em andamento daquele site termina;
  - enquanto isso o scheduler segue despachando URLs dos outros sites.
Se o coletor for reiniciado, nada se perde: as URLs continuam "na fila" no
taskdb e são recarregadas.
"""
from __future__ import unicode_literals

import collections
import os
import time

MAX_EM_ANDAMENTO_POR_SITE = 6
PRAZO_PERDIDA_S = int(os.environ.get('RIW_PRAZO_PERDIDA', 30 * 60))  # antes: 10 min
VALIDADE_ANDAMENTO_S = 15 * 60  # URL sem resposta há mais tempo libera a vaga
VARREDURA_S = 30                # de quanto em quanto tempo revisa vagas presas


def aplicar_correcao():
    from six.moves.urllib.parse import urlsplit
    from pyspider.scheduler.scheduler import Scheduler
    from pyspider.scheduler.task_queue import TaskQueue

    TaskQueue.processing_timeout = PRAZO_PERDIDA_S

    original_status = Scheduler.on_task_status
    original_select = Scheduler._check_select

    def _estado(self):
        if not hasattr(self, '_riw_andamento'):
            self._riw_andamento = collections.defaultdict(dict)   # host -> {taskid: t}
            self._riw_espera = collections.defaultdict(collections.OrderedDict)  # host -> {(proj, tid)}
            self._riw_host = {}                                   # taskid -> host
            self._riw_varredura = 0
        return self

    def _limpar(self, host):
        agora = time.time()
        do_site = self._riw_andamento[host]
        for tid in [t for t, inicio in do_site.items() if agora - inicio > VALIDADE_ANDAMENTO_S]:
            del do_site[tid]
            self._riw_host.pop(tid, None)

    def _enviar(self, project, taskid, host):
        try:
            task = self.taskdb.get_task(project, taskid, fields=self.request_task_fields)
        except ValueError:
            return
        if not task or task.get('status') not in (None, self.taskdb.ACTIVE):
            return  # já concluída / removida
        self._riw_andamento[host][taskid] = time.time()
        self._riw_host[taskid] = host
        self.on_select_task(task)

    def _preencher(self, host):
        """Envia URLs em espera do site enquanto houver vaga."""
        _limpar(self, host)
        espera = self._riw_espera.get(host)
        while espera and len(self._riw_andamento[host]) < MAX_EM_ANDAMENTO_POR_SITE:
            (project, taskid), _ = espera.popitem(last=False)
            _enviar(self, project, taskid, host)
        if host in self._riw_espera and not self._riw_espera[host]:
            del self._riw_espera[host]

    def _load_put_task(self, project, taskid):
        _estado(self)
        try:
            task = self.taskdb.get_task(project, taskid, fields=['url'])
        except ValueError:
            return
        if not task:
            return
        url = task.get('url') or ''
        if url.startswith('data:'):
            return Scheduler._riw_original_load(self, project, taskid)

        host = urlsplit(url).netloc.lower()
        # tira do controle de prazo do pyspider: quem controla agora somos nós
        self.projects[project].task_queue.done(taskid)
        if taskid in self._riw_andamento[host]:
            return  # já está em andamento
        self._riw_espera[host][(project, taskid)] = True
        _preencher(self, host)

    def on_task_status(self, task):
        _estado(self)
        taskid = task.get('taskid')
        host = self._riw_host.pop(taskid, None)
        if host is not None:
            self._riw_andamento[host].pop(taskid, None)
            # para o pyspider aceitar o resultado, a URL precisa constar como
            # "em processamento" na fila dele
            fila = self.projects[task['project']].task_queue
            if taskid not in fila.processing:
                from pyspider.scheduler.task_queue import InQueueTask
                fila.processing.put(InQueueTask(taskid, 0, time.time() + PRAZO_PERDIDA_S))
        ret = original_status(self, task)
        if host is not None:
            _preencher(self, host)
        return ret

    def _check_select(self):
        _estado(self)
        agora = time.time()
        if agora - self._riw_varredura > VARREDURA_S:
            self._riw_varredura = agora
            for host in list(self._riw_espera.keys()):
                _preencher(self, host)
        return original_select(self)

    Scheduler._riw_original_load = Scheduler._load_put_task
    Scheduler._load_put_task = _load_put_task
    Scheduler.on_task_status = on_task_status
    Scheduler._check_select = _check_select
