# Coletor RIW: promessas e planos de governo (Eleições 2026)

Etapa 1 (coleta) do sistema de Recuperação de Informação. O coletor usa o
**pyspider**. Tudo o que ele guarda fica **em arquivo**, sem banco relacional
nem não relacional, inclusive a fila interna de URLs do pyspider.

## Instalação

É preciso ter **Python 3.8**, porque o pyspider não roda nas versões mais novas.
No Linux e no macOS, instale também a libcurl de desenvolvimento
(`sudo apt install libcurl4-openssl-dev libssl-dev`).

```bash
python3.8 -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install -r requirements.txt
pip install --no-deps "git+https://github.com/binux/pyspider.git@897891cafb21ea5b4ac08e728ad2ea212879f7fa"
```

Por que instalar assim:
- **Versão do pyspider:** a do PyPI (0.3.10) quebra a partir do Python 3.7, porque
  `async` virou palavra reservada. A versão do GitHub corrige isso.
- **Flag `--no-deps`:** sem ela, o pip instalaria as versões antigas que o pyspider
  fixa, que já não compilam. As versões que funcionam estão em `requirements.txt`.

**No Windows**, o `pycurl` costuma dar trabalho para instalar. A saída mais simples
é usar o Docker. O `Dockerfile` está pronto, mas ainda não foi testado:

```bash
docker build -t riw-coletor .
docker run --rm -it -p 5000:5000 -v "$(pwd)/dados:/app/dados" riw-coletor
```

## Uso

```bash
# 1. editar o e-mail de contato (vai no User-Agent) e conferir as sementes
export RIW_CONTATO="email-do-grupo@..."

python coletar.py iniciar                        # inicia ou RETOMA a coleta
python coletar.py iniciar --projetos promessas.py   # só as notícias
python coletar.py status                         # escala: nº de documentos por tipo e fonte
python extrair_pdfs.py                           # extrai o texto dos PDFs coletados
```

- **Painel:** a WebUI do pyspider fica em http://localhost:5000. Ela mostra o
  progresso, as filas e os resultados, o que é útil na apresentação.
- **Parar e retomar:** para parar, use **Ctrl+C**. Ao rodar `iniciar` de novo, a coleta
  continua de onde parou e não baixa de novo o que ainda está dentro do prazo de revisita.

Parâmetros por variável de ambiente:

| Variável | Padrão | O que controla |
|---|---|---|
| `RIW_META` | 60000 | critério de parada (nº de documentos) |
| `RIW_PROFUNDIDADE` | 4 | profundidade máxima a partir das sementes |
| `RIW_LIMIAR` | 4 | pontuação mínima de relevância para gravar a página |
| `RIW_DADOS` | `./dados` | onde ficam fila e coleta |
| `RIW_CONFIG` | `./config` | onde ficam sementes, sitemaps e domínios |
| `RIW_CORTE` | 2026-01-01 | filtro temporal dos sitemaps (ignora matérias com `<lastmod>` anterior) |
| `RIW_TSE_ANOS` | 2026 | anos coletados no TSE (ex.: `2026,2022`) |

## Estrutura

```
coletar.py              lançador: registra os bancos em arquivo e sobe o pyspider
extrair_pdfs.py         texto dos PDFs (pypdf)
projetos/
  promessas.py          coletor focado em notícias e checagens (sementes + sitemaps)
  tse_planos.py         candidaturas e propostas de governo no DivulgaCandContas
riw/
  filedb.py             taskdb e resultdb do pyspider implementados em arquivo
  texto.py              extração de texto do HTML e cálculo de relevância
  urls.py               normalização e filtros de URL
  config.py             parâmetros
  pdfs.py               gravação e leitura de PDFs
config/
  sementes.txt  sitemaps.txt  dominios.txt
```

## Formato dos dados (`dados/`)

```
estado/promessas.jsonl                   fila do crawler (journal, 1 operação por linha)
coleta/promessas/paginas/lote-00001.jsonl.gz   documentos, 1.000 por lote, gzip
coleta/promessas/manifesto.tsv           1 linha por documento: id, url, lote, linha, tipo, fonte...
coleta/promessas/duplicatas.tsv          páginas descartadas por conteúdo repetido
coleta/promessas/contagem.json           contagem atual (usada no critério de parada)
coleta/promessas/pdfs/<md5>.pdf          PDFs brutos (+ <md5>.json com o texto)
```

Cada documento é uma linha JSON com estes campos: `id`, `url`, `url_final`,
`url_origem`, `profundidade`, `data_coleta`, `status_http`, `tipo` (html, pdf ou
candidato), `fonte`, `titulo`, `descricao`, `data_publicacao`, `autor`, `texto`,
`relevancia`, `termos` e `hash_conteudo`.

**Escala:** o total de documentos é `wc -l manifesto.tsv` menos 1 (o cabeçalho),
ou a saída de `python coletar.py status`.

---

## Decisões de projeto (base para o relatório)

### Como o pyspider funciona sem banco de dados
O pyspider guarda o estado em três "bancos". Os três foram trocados por arquivos:

| Banco do pyspider | Padrão | Aqui |
|---|---|---|
| projectdb (código) | SQLite | `local+projectdb://projetos/*.py`, modo nativo que lê os `.py` |
| taskdb (fila e revisita) | SQLite | `FileTaskDB`: **journal** JSONL, só acrescenta no fim |
| resultdb (documentos) | SQLite | `FileResultDB`: **lotes JSONL + manifesto TSV** |

O lançador ensina o pyspider a abrir os endereços `file+taskdb://` e
`file+resultdb://`. As classes seguem exatamente as interfaces de
`pyspider/database/base/`.

- **Journal:** cada inserção ou atualização de tarefa vira uma linha no fim do
  arquivo. Ao reiniciar, o arquivo é relido e o estado é reconstruído. É a mesma
  ideia do *write-ahead log* dos bancos. Antes de cada início, o journal é
  **compactado** e fica só com o estado final de cada URL.
- **Vários processos:** o `pyspider all` roda scheduler, fetcher, processor,
  result worker e WebUI em processos separados. Só o scheduler escreve na fila
  e só o result worker escreve nos resultados. Cada escrita é um `write()` em
  modo append com trava (`flock`), e os leitores acompanham o arquivo a partir
  do último byte lido.

### Tipo de coletor
É um **coletor focado (topical crawler)** e **incremental**:
- **Focado:** só expande links de páginas relevantes para o tema e grava apenas
  páginas relevantes. A relevância é calculada por termos de promessa ("plano de
  governo", "promete", "propostas", "se eleito"...), com peso 3, e por termos de
  contexto eleitoral ("candidato", "governador", "debate", "sabatina"...), com
  peso 1. Termos no título valem em dobro.
- **Incremental:** revisita as páginas conforme a política de revisita, em vez de
  recomeçar do zero a cada execução.

### Arquitetura
A arquitetura é a do pyspider:
- **Scheduler:** mantém a fronteira, as prioridades e a revisita.
- **Fetcher:** faz o download de forma assíncrona (tornado + libcurl).
- **Processor:** roda o parsing e a política de seleção.
- **Result worker:** grava os resultados.

Os componentes se comunicam por filas.

### Políticas
- **Seleção:**
  - **Sementes:** seções de eleições dos portais nacionais e regionais e as
    agências de checagem.
  - **Sitemaps:** os sitemaps dos portais entram como fonte extra, mas só as URLs
    com termos do tema e com data (`<lastmod>`) a partir de `RIW_CORTE`
    (filtro temporal; num índice de sitemaps, arquivos inteiros antigos nem são baixados).
  - **Filtros:** por domínio, por extensão e por seções fora do tema (esportes,
    novelas...), inclusive quando a seção está no 1º nível do caminho (ex.:
    `/poder-sportsmkt/`). Nos sitemaps, o filtro de termos é mais restrito:
    termos ambíguos, como "presidente" (de clube ou de empresa), não bastam. Os sitemaps
    de tags, categorias e autores são ignorados, porque não listam matérias.
  - **PDFs:** só são baixados quando a URL ou o texto do link têm termos como
    plano, governo, proposta ou eleição, e só são gravados se o texto das 5
    primeiras páginas passar no filtro de relevância. Isso descarta editais e
    balanços que os jornais publicam em PDF.
  - **Prioridade:** PDFs têm prioridade 7. As URLs recebem prioridade conforme os
    termos presentes na URL e no texto do link.
- **Revisita:** o parâmetro `age` define o prazo de cada tipo:
  - capas e seções: 6 h;
  - notícias: 30 dias;
  - PDFs: 30 dias.

  Trocar o `itag` força a recoleta de tudo. Os cabeçalhos ETag e Last-Modified
  ficam guardados para revisitas condicionais.
- **Cortesia:**
  - O `robots.txt` é respeitado (`robots_txt: True`, com checagem no fetcher).
  - O User-Agent identifica o coletor e traz um e-mail de contato.
  - **Limite por site** (`riw/cortesia.py`): no máximo 2 conexões simultâneas
    e 0,5 s entre pedidos ao mesmo site. O pyspider só limita a taxa do projeto
    inteiro, e sem esse limite ele chegava a abrir dezenas de conexões com o mesmo
    portal, que reagia reduzindo a velocidade e depois recusando conexões.
  - **Justiça entre sites no scheduler** (`riw/agendamento.py`): no máximo 6 URLs
    em andamento por site. As excedentes esperam numa fila própria do site e são
    enviadas quando uma termina. Sem isso, um site com milhares de URLs seguidas
    na fila monopolizava a coleta, e as URLs que esperavam mais de 10 minutos eram
    reenviadas pelo pyspider ("processing: retry"), o que fazia a mesma página ser
    baixada várias vezes.
  - A taxa é limitada por projeto: `# rate: 4` e `# burst: 8` nas notícias,
    e `# rate: 1` no TSE.
- **Paralelização:** fetcher assíncrono. É possível aumentar o número de processos
  com `python coletar.py iniciar --fetchers 2 --processadores 2`.

### Tolerâncias
- **Falhas de rede:** `retries: 3`, com espera crescente entre as tentativas, e
  timeouts de conexão (20 s) e total (90 s).
- **Erros HTTP:** respostas 4xx e 5xx ficam registradas na fila como falha.
- **Duplicatas de URL:** a URL é normalizada antes de virar identificador. O
  host vai para minúsculas, o fragmento `#` é removido, parâmetros de
  rastreamento (`utm_*`, `fbclid`...) são descartados e a query é ordenada.
- **Duplicatas de conteúdo:** usa-se o hash do texto normalizado. A página
  repetida vai para `duplicatas.tsv` em vez de ser gravada.
- **robots.txt com erro:** o pyspider original quebrava quando o robots.txt dava
  erro, e com isso todas as URLs do site falhavam. `riw/robots.py` corrige:
  com 404, o site é tratado como sem restrições; com 5xx ou falha de rede, o site
  fica pausado e o robots.txt é lido de novo em 10 minutos (RFC 9309).
- **Armadilhas:** URLs com mais de 400 caracteres ou mais de 12 níveis são
  descartadas, e há limite de profundidade.
- **Arquivos grandes e inválidos:** PDFs acima de 30 MB são descartados, e só são
  aceitos arquivos que começam com `%PDF`.
- **Queda do processo:** perde-se no máximo a linha que estava sendo escrita, e o
  leitor ignora linhas incompletas. A coleta é retomada pelo journal.

### Critério de parada
- **Meta de documentos:** com `RIW_META` documentos gravados, o coletor para de
  enfileirar URLs novas. O que já está na fila termina, então o número final
  passa um pouco da meta.
- **Profundidade máxima:** `RIW_PROFUNDIDADE`.
- **Fim da fronteira:** a coleta também termina quando não há mais URLs novas.

---

## Testado
O coletor foi testado com um site local contendo notícias, sitemap, `robots.txt`,
PDF, página duplicada, página fora do tema e página com erro 500:
- **Escala do teste:** 46 documentos gravados, que era o esperado.
- **robots.txt:** a página bloqueada não chegou a ser pedida ao servidor.
- **Duplicatas:** a URL com `utm_source` não foi baixada de novo, e a cópia de
  conteúdo foi para `duplicatas.tsv`.
- **Fora do tema:** a página foi baixada, mas não gravada nem expandida.
- **Erro 500:** gerou nova tentativa.
- **Retomada:** depois de reiniciar, só a semente foi pedida de novo.
- **Critério de parada:** com `RIW_META=10`, a coleta parou de expandir e gravou
  34 documentos, contra 46 sem a meta.
- **Lotes:** a rotação e a compactação em gzip funcionaram.
- **WebUI:** a leitura dos resultados funcionou, com a WebUI rodando em outro processo.
- **Modos de execução:** os modos subprocess e `--threads` funcionaram.

## Pendências antes da coleta real
1. **Sementes:** abrir cada URL de `config/sementes.txt` e `config/sitemaps.txt` no
   navegador e trocar as que mudaram de endereço.
2. **API do TSE:** conferir os endpoints em `projetos/tse_planos.py` (constantes
   `API_*`). Abram um candidato no DivulgaCandContas com as DevTools, na aba Network.
3. **Contato:** definir `RIW_CONTATO` com o e-mail do grupo.
4. **Tempo de coleta:** a 4 páginas/s, 60 mil páginas levam cerca de 4 a 5 horas,
   sem contar as páginas baixadas e descartadas. Comecem com antecedência.
