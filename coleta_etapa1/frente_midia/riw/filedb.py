# -*- coding: utf-8 -*-
"""
Backends do pyspider em ARQUIVO (sem banco relacional ou não relacional).

O pyspider guarda o estado em três "bancos":
  - taskdb    -> fila de URLs / controle de revisita      => FileTaskDB   (journal JSONL)
  - resultdb  -> documentos coletados                      => FileResultDB (lotes JSONL + manifesto TSV)
  - projectdb -> código do crawler                         => nativo do pyspider: local+projectdb://projetos/*.py

Os dois primeiros são implementados aqui, seguindo as interfaces de
pyspider/database/base/taskdb.py e pyspider/database/base/resultdb.py.

Formato em disco
----------------
estado/<projeto>.jsonl        (FileTaskDB)
    Um "journal" (log de operações, só acrescenta no fim). Cada linha é uma
    operação: {"op": "i"|"u", "id": taskid, "o": {...campos...}}
    Ao reiniciar, o arquivo é relido do início e o estado é reconstruído na
    memória. A compactação (compactar_taskdb) reescreve o journal só com o
    estado final de cada tarefa.

coleta/<projeto>/paginas/lote-00001.jsonl.gz   (FileResultDB)
    Um documento por linha, em lotes de TAMANHO_LOTE documentos. O lote em
    andamento fica sem compactar (.jsonl) e é compactado (gzip) ao encher.
coleta/<projeto>/manifesto.tsv
    Uma linha por documento gravado: onde ele está (lote + linha) e metadados.
    É o "índice" da coleta: contar linhas = medir a escala.
coleta/<projeto>/duplicatas.tsv
    Páginas descartadas por terem conteúdo idêntico a outra já gravada.
coleta/<projeto>/contagem.json
    Contagem atual, lida pelo crawler para o critério de parada.

Concorrência
------------
No modo `pyspider all` cada componente pode rodar em um processo separado.
Só o scheduler escreve no taskdb e só o result_worker escreve no resultdb;
os demais processos (ex.: webui) apenas leem. Toda escrita é um único
write() em modo append protegido por flock, e os leitores acompanham o
arquivo a partir do último byte lido ("tail"), então todos enxergam o mesmo
estado sem precisar de um servidor de banco.
"""
from __future__ import unicode_literals

import copy
import gzip
import io
import json
import os
import threading
import time

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None

from pyspider.database.base.taskdb import TaskDB as BaseTaskDB
from pyspider.database.base.resultdb import ResultDB as BaseResultDB

TAMANHO_LOTE = 1000
FSYNC_A_CADA = 2.0  # segundos


def _dumps(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(',', ':'), default=_json_default)


def _json_default(o):
    if isinstance(o, bytes):
        return o.decode('utf-8', 'replace')
    if isinstance(o, (set, frozenset)):
        return list(o)
    return str(o)


class _Append(object):
    """Arquivo em modo append com trava (flock) e fsync periódico."""

    def __init__(self, path):
        self.path = path
        self.fp = None
        self._ultimo_fsync = 0

    def write(self, texto):
        if self.fp is None:
            self.fp = io.open(self.path, 'ab')
        dados = texto.encode('utf-8')
        if fcntl:
            fcntl.flock(self.fp.fileno(), fcntl.LOCK_EX)
        try:
            inicio = os.fstat(self.fp.fileno()).st_size
            self.fp.write(dados)
            self.fp.flush()
            agora = time.time()
            if agora - self._ultimo_fsync > FSYNC_A_CADA:
                os.fsync(self.fp.fileno())
                self._ultimo_fsync = agora
        finally:
            if fcntl:
                fcntl.flock(self.fp.fileno(), fcntl.LOCK_UN)
        return inicio, len(dados)

    def close(self):
        if self.fp is not None:
            try:
                self.fp.flush()
                os.fsync(self.fp.fileno())
            except (OSError, ValueError):
                pass
            self.fp.close()
            self.fp = None


class _Tail(object):
    """Lê apenas as linhas completas acrescentadas desde a última leitura."""

    def __init__(self, path):
        self.path = path
        self.offset = 0

    def novas_linhas(self):
        try:
            tamanho = os.path.getsize(self.path)
        except OSError:
            return []
        if tamanho < self.offset:  # arquivo foi compactado/substituído
            self.offset = 0
            raise _ArquivoTrocado()
        if tamanho == self.offset:
            return []
        with io.open(self.path, 'rb') as fp:
            fp.seek(self.offset)
            bloco = fp.read(tamanho - self.offset)
        fim = bloco.rfind(b'\n')
        if fim < 0:
            return []  # linha ainda incompleta
        self.offset += fim + 1
        return [l for l in bloco[:fim].split(b'\n') if l.strip()]


class _ArquivoTrocado(Exception):
    pass


# ---------------------------------------------------------------------------
# taskdb
# ---------------------------------------------------------------------------

def _enxugar_task(obj):
    """Remove do registro o que não é necessário para agendar/revisitar.

    O processor devolve em track.fetch.content o início da página e em
    track.process.logs os logs do callback; guardar isso para centenas de
    milhares de URLs incharia o journal e a memória sem utilidade para o
    scheduler. Mantemos só os cabeçalhos usados na revisita condicional
    (ETag / Last-Modified).
    """
    track = obj.get('track')
    if not isinstance(track, dict):
        return obj
    obj = dict(obj)
    track = dict(track)
    fetch = dict(track.get('fetch') or {})
    fetch.pop('content', None)
    headers = fetch.get('headers') or {}
    if isinstance(headers, dict):
        fetch['headers'] = dict((k, v) for k, v in headers.items()
                                if k.lower() in ('etag', 'last-modified', 'content-type'))
    track['fetch'] = fetch
    process = dict(track.get('process') or {})
    if process.get('logs'):
        process['logs'] = process['logs'][-500:]
    if process.get('exception'):
        process['exception'] = str(process['exception'])[:500]
    track['process'] = process
    obj['track'] = track
    return obj


class _Projeto(object):
    def __init__(self, path):
        self.tail = _Tail(path)
        self.writer = _Append(path)
        self.tasks = {}
        self.contagem = {}

    def aplicar(self, linha):
        try:
            rec = json.loads(linha.decode('utf-8'))
        except ValueError:
            return  # linha corrompida (ex.: queda no meio da escrita)
        taskid, obj = rec.get('id'), rec.get('o') or {}
        antigo = self.tasks.get(taskid)
        status_antigo = antigo.get('status') if antigo else None
        if rec.get('op') == 'i' or antigo is None:
            self.tasks[taskid] = obj
            novo = obj
        else:
            antigo.update(obj)
            novo = antigo
        status_novo = novo.get('status')
        if status_antigo != status_novo:
            if status_antigo is not None:
                self.contagem[status_antigo] -= 1
            if status_novo is not None:
                self.contagem[status_novo] = self.contagem.get(status_novo, 0) + 1

    def sincronizar(self):
        try:
            linhas = self.tail.novas_linhas()
        except _ArquivoTrocado:
            self.tasks, self.contagem = {}, {}
            linhas = self.tail.novas_linhas()
        for linha in linhas:
            self.aplicar(linha)


class FileTaskDB(BaseTaskDB):
    """taskdb do pyspider gravado em arquivos journal (um por projeto)."""

    def __init__(self, path):
        self.path = os.path.abspath(path)
        if not os.path.isdir(self.path):
            os.makedirs(self.path)
        self._projetos = {}
        self._lock = threading.RLock()

    # -- auxiliares --------------------------------------------------------
    def _arquivo(self, project):
        return os.path.join(self.path, '%s.jsonl' % project)

    def _projeto(self, project):
        with self._lock:
            p = self._projetos.get(project)
            if p is None:
                p = self._projetos[project] = _Projeto(self._arquivo(project))
            p.sincronizar()
            return p

    @staticmethod
    def _campos(task, fields):
        if not fields:
            return copy.deepcopy(task)
        return dict((f, copy.deepcopy(task[f])) for f in fields if f in task)

    def _gravar(self, project, op, taskid, obj):
        with self._lock:
            p = self._projetos.get(project) or self._projeto(project)
            linha = _dumps({'op': op, 'id': taskid, 'o': obj})
            inicio, n = p.writer.write(linha + '\n')
            if p.tail.offset == inicio:
                # ninguém mais escreveu desde a última leitura: aplica direto na
                # memória, sem reabrir o arquivo (reabrir a cada gravação é muito
                # lento no Windows, onde o antivírus examina o arquivo a cada abertura)
                p.aplicar(linha.encode('utf-8'))
                p.tail.offset = inicio + n
            else:
                p.sincronizar()

    # -- interface TaskDB ---------------------------------------------------
    @property
    def projects(self):
        nomes = set()
        for nome in os.listdir(self.path):
            if nome.endswith('.jsonl'):
                nomes.add(nome[:-len('.jsonl')])
        return nomes

    def load_tasks(self, status, project=None, fields=None):
        if project is None:
            projetos = self.projects
        elif isinstance(project, (list, set, tuple)):
            projetos = project
        else:
            projetos = [project]
        for nome in projetos:
            p = self._projeto(nome)
            with self._lock:
                selecionadas = [self._campos(t, fields) for t in p.tasks.values()
                                if t.get('status') == status]
            for t in selecionadas:
                yield t

    def get_task(self, project, taskid, fields=None):
        p = self._projeto(project)
        with self._lock:
            task = p.tasks.get(taskid)
            return self._campos(task, fields) if task is not None else None

    def status_count(self, project):
        p = self._projeto(project)
        with self._lock:
            return dict((k, v) for k, v in p.contagem.items() if v > 0)

    def insert(self, project, taskid, obj={}):
        obj = _enxugar_task(dict(obj))
        obj['taskid'] = taskid
        obj['project'] = project
        obj['updatetime'] = time.time()
        obj.setdefault('status', self.ACTIVE)
        self._gravar(project, 'i', taskid, obj)

    def update(self, project, taskid, obj={}, **kwargs):
        obj = dict(obj)
        obj.update(kwargs)
        obj = _enxugar_task(obj)
        obj['updatetime'] = time.time()
        self._gravar(project, 'u', taskid, obj)

    def drop(self, project):
        with self._lock:
            p = self._projetos.pop(project, None)
            if p is not None:
                p.writer.close()
            try:
                os.remove(self._arquivo(project))
            except OSError:
                pass

    def copy(self):
        return FileTaskDB(self.path)


def compactar_taskdb(path):
    """Reescreve cada journal só com o estado final de cada tarefa.

    Deve rodar com o crawler PARADO (o lançador faz isso antes de iniciar).
    Retorna {projeto: (linhas_antes, tarefas_depois)}.
    """
    resumo = {}
    if not os.path.isdir(path):
        return resumo
    for nome in sorted(os.listdir(path)):
        if not nome.endswith('.jsonl'):
            continue
        arquivo = os.path.join(path, nome)
        p = _Projeto(arquivo)
        with io.open(arquivo, 'rb') as fp:
            linhas = 0
            for linha in fp:
                if linha.endswith(b'\n') and linha.strip():
                    linhas += 1
                    p.aplicar(linha.rstrip(b'\n'))
        tmp = arquivo + '.tmp'
        with io.open(tmp, 'wb') as out:
            for taskid, task in p.tasks.items():
                out.write((_dumps({'op': 'i', 'id': taskid, 'o': task}) + '\n').encode('utf-8'))
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, arquivo)
        resumo[nome[:-len('.jsonl')]] = (linhas, len(p.tasks))
    return resumo


# ---------------------------------------------------------------------------
# resultdb
# ---------------------------------------------------------------------------

COLUNAS_MANIFESTO = ['id', 'url', 'lote', 'linha', 'data_coleta', 'tipo', 'fonte',
                     'bytes_texto', 'hash_conteudo', 'situacao']


def _tentar(func, *args):
    """No Windows, trocar/apagar um arquivo que outro leitor tem aberto falha
    (PermissionError). Tenta algumas vezes e, se não der, segue sem derrubar
    o result worker: a contagem é regravada no próximo documento."""
    for _ in range(20):
        try:
            return func(*args)
        except OSError:
            time.sleep(0.05)
    return None


def _abrir_lote(caminho):
    if caminho.endswith('.gz'):
        return io.TextIOWrapper(gzip.open(caminho, 'rb'), encoding='utf-8')
    return io.open(caminho, 'r', encoding='utf-8')


class _ColecaoResultados(object):
    """Estado de um projeto no resultdb (índice em memória + escritor)."""

    def __init__(self, raiz):
        self.raiz = raiz
        self.dir_paginas = os.path.join(raiz, 'paginas')
        if not os.path.isdir(self.dir_paginas):
            os.makedirs(self.dir_paginas)
        self.manifesto = os.path.join(raiz, 'manifesto.tsv')
        if not os.path.exists(self.manifesto):
            with io.open(self.manifesto, 'w', encoding='utf-8') as fp:
                fp.write('\t'.join(COLUNAS_MANIFESTO) + '\n')
        self.tail = _Tail(self.manifesto)
        self.indice = {}        # id -> linha do manifesto (dict), versão mais recente
        self.ordem = []         # ids na ordem da primeira gravação
        self.hashes = {}        # hash_conteudo -> id
        self.duplicatas = 0
        self.escritor = None    # inicializado só no processo que grava
        self.sincronizar()

    def sincronizar(self):
        for linha in self.tail.novas_linhas():
            self._registrar(linha.decode('utf-8'))

    def _registrar(self, linha):
        campos = linha.split('\t')
        if campos[0] == 'id' or len(campos) != len(COLUNAS_MANIFESTO):
            return
        reg = dict(zip(COLUNAS_MANIFESTO, campos))
        reg['linha'] = int(reg['linha'])
        if reg['id'] not in self.indice:
            self.ordem.append(reg['id'])
        self.indice[reg['id']] = reg
        if reg['hash_conteudo']:
            self.hashes[reg['hash_conteudo']] = reg['id']

    # -- escrita -----------------------------------------------------------
    def _iniciar_escritor(self):
        lotes = sorted(n for n in os.listdir(self.dir_paginas) if n.startswith('lote-'))
        numero, linhas = 1, 0
        if lotes:
            ultimo = lotes[-1]
            numero = int(ultimo.split('-')[1].split('.')[0])
            if ultimo.endswith('.gz'):
                numero += 1
            else:
                with io.open(os.path.join(self.dir_paginas, ultimo), 'rb') as fp:
                    linhas = sum(1 for l in fp if l.endswith(b'\n'))
        dup = os.path.join(self.raiz, 'duplicatas.tsv')
        if os.path.exists(dup):
            with io.open(dup, 'rb') as fp:
                self.duplicatas = max(0, sum(1 for _ in fp) - 1)
        else:
            with io.open(dup, 'w', encoding='utf-8') as fp:
                fp.write('url\tid\tduplicata_de\tdata_coleta\n')
        self.escritor = {
            'numero': numero,
            'linhas': linhas,
            'lote': None,
            'manifesto': _Append(self.manifesto),
            'duplicatas': _Append(dup),
            'ultima_contagem': 0,
        }
        if linhas >= TAMANHO_LOTE:
            self._fechar_lote()

    def _nome_lote(self, numero, gz=False):
        return 'lote-%05d.jsonl%s' % (numero, '.gz' if gz else '')

    def _fechar_lote(self):
        """Compacta o lote cheio (gzip) e passa para o próximo."""
        e = self.escritor
        if e['lote'] is not None:
            e['lote'].close()
            e['lote'] = None
        origem = os.path.join(self.dir_paginas, self._nome_lote(e['numero']))
        destino = os.path.join(self.dir_paginas, self._nome_lote(e['numero'], gz=True))
        if os.path.exists(origem):
            with io.open(origem, 'rb') as fi, gzip.open(destino + '.tmp', 'wb') as fo:
                fo.write(fi.read())
            os.replace(destino + '.tmp', destino)
            _tentar(os.remove, origem)
            # o manifesto aponta para o nome sem .gz; a leitura tenta os dois
        e['numero'] += 1
        e['linhas'] = 0

    def gravar(self, taskid, url, result):
        if self.escritor is None:
            self._iniciar_escritor()
        self.sincronizar()
        e = self.escritor
        agora = time.time()
        result = dict(result) if isinstance(result, dict) else {'valor': result}
        h = result.get('hash_conteudo') or ''
        anterior = self.indice.get(taskid)

        # deduplicação por conteúdo
        if h and h in self.hashes:
            dono = self.hashes[h]
            if dono != taskid:
                e['duplicatas'].write('\t'.join([url, taskid, dono, _iso(agora)]) + '\n')
                self.duplicatas += 1
                self._atualizar_contagem()
                return False
            if anterior is not None:
                return False  # revisita sem mudança de conteúdo

        situacao = 'novo' if anterior is None else 'atualizado'
        doc = {'id': taskid, 'url': url, 'data_coleta': _iso(agora),
               'updatetime': agora}
        doc.update(result)
        if e['lote'] is None:
            e['lote'] = _Append(os.path.join(self.dir_paginas, self._nome_lote(e['numero'])))
        e['lote'].write(_dumps(doc) + '\n')
        linha = e['linhas']
        e['linhas'] += 1
        texto = result.get('texto') or ''
        campos = {
            'id': taskid, 'url': _limpar_tsv(url), 'lote': self._nome_lote(e['numero']),
            'linha': str(linha), 'data_coleta': _iso(agora),
            'tipo': _limpar_tsv(result.get('tipo', '')),
            'fonte': _limpar_tsv(result.get('fonte', '')),
            'bytes_texto': str(len(texto.encode('utf-8'))), 'hash_conteudo': h,
            'situacao': situacao,
        }
        linha_manifesto = '\t'.join(campos[c] for c in COLUNAS_MANIFESTO)
        inicio, n = e['manifesto'].write(linha_manifesto + '\n')
        if self.tail.offset == inicio:
            self._registrar(linha_manifesto)   # sem reabrir o manifesto
            self.tail.offset = inicio + n
        else:
            self.sincronizar()
        if e['linhas'] >= TAMANHO_LOTE:
            self._fechar_lote()
        self._atualizar_contagem()
        return True

    def _atualizar_contagem(self):
        e = self.escritor
        agora = time.time()
        e['ultima_contagem'] = agora
        dados = {'documentos': len(self.indice), 'duplicatas_descartadas': self.duplicatas,
                 'atualizado_em': _iso(agora)}
        tmp = os.path.join(self.raiz, 'contagem.json.tmp')
        with io.open(tmp, 'w', encoding='utf-8') as fp:
            fp.write(_dumps(dados))
        _tentar(os.replace, tmp, os.path.join(self.raiz, 'contagem.json'))

    def fechar(self):
        if self.escritor:
            self._atualizar_contagem()
            for k in ('lote', 'manifesto', 'duplicatas'):
                if self.escritor.get(k):
                    self.escritor[k].close()

    # -- leitura -----------------------------------------------------------
    def ler(self, regs):
        """Lê os documentos apontados por uma lista de linhas do manifesto."""
        por_lote = {}
        for r in regs:
            por_lote.setdefault(r['lote'], set()).add(r['linha'])
        docs = {}
        for lote, linhas in por_lote.items():
            caminho = os.path.join(self.dir_paginas, lote)
            if not os.path.exists(caminho):
                caminho += '.gz'
            if not os.path.exists(caminho):
                continue
            with _abrir_lote(caminho) as fp:
                for i, linha in enumerate(fp):
                    if i in linhas:
                        try:
                            docs[(lote, i)] = json.loads(linha)
                        except ValueError:
                            pass
                    if i >= max(linhas):
                        break
        return [docs.get((r['lote'], r['linha'])) for r in regs]


def _iso(ts):
    return time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(ts))


def _limpar_tsv(valor):
    return str(valor).replace('\t', ' ').replace('\n', ' ').replace('\r', ' ')


class FileResultDB(BaseResultDB):
    """resultdb do pyspider gravado em lotes JSONL + manifesto TSV."""

    def __init__(self, path):
        self.path = os.path.abspath(path)
        if not os.path.isdir(self.path):
            os.makedirs(self.path)
        self._colecoes = {}
        self._lock = threading.RLock()

    def _colecao(self, project):
        with self._lock:
            c = self._colecoes.get(project)
            if c is None:
                c = self._colecoes[project] = _ColecaoResultados(os.path.join(self.path, project))
            else:
                c.sincronizar()
            return c

    @property
    def projects(self):
        return set(n for n in os.listdir(self.path)
                   if os.path.isfile(os.path.join(self.path, n, 'manifesto.tsv')))

    def save(self, project, taskid, url, result):
        with self._lock:
            return self._colecao(project).gravar(taskid, url, result)

    def _formatar(self, reg, doc, fields):
        if doc is None:
            return None
        item = {'taskid': reg['id'], 'url': doc.get('url'), 'result': doc,
                'updatetime': doc.get('updatetime')}
        if fields:
            item = dict((k, v) for k, v in item.items() if k in fields)
        return item

    def select(self, project, fields=None, offset=0, limit=None):
        with self._lock:
            c = self._colecao(project)
            ids = c.ordem[offset:(offset + limit) if limit else None]
            regs = [c.indice[i] for i in ids]
        for inicio in range(0, len(regs), 500):
            parte = regs[inicio:inicio + 500]
            for reg, doc in zip(parte, c.ler(parte)):
                item = self._formatar(reg, doc, fields)
                if item is not None:
                    yield item

    def count(self, project):
        with self._lock:
            return len(self._colecao(project).indice)

    def get(self, project, taskid, fields=None):
        with self._lock:
            if project not in self.projects:
                return None
            c = self._colecao(project)
            reg = c.indice.get(taskid)
            if reg is None:
                return None
            return self._formatar(reg, c.ler([reg])[0], fields)

    def drop(self, project):
        raise NotImplementedError('apague a pasta coleta/%s manualmente' % project)

    def close(self):
        with self._lock:
            for c in self._colecoes.values():
                c.fechar()

    def copy(self):
        return FileResultDB(self.path)
