// Interface do Radar 2026: chama a API do buscador.py e monta a página.
// JavaScript puro (sem framework). O estado da busca fica na URL (?q=...&ano=...),
// então o botão Voltar do navegador e os links copiados funcionam.
(function () {
  'use strict';

  var estado = { q: '', pagina: 1, modo: 'ou', ano: [], fonte: '', origem: '', modelo: 'bm25', reforco: '1' };
  var info = null;
  var $ = function (s) { return document.querySelector(s); };
  var fmt = function (n) { return Number(n).toLocaleString('pt-BR'); };

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  // ---------- URL <-> estado ----------
  function lerURL() {
    var p = new URLSearchParams(location.search);
    estado.q = p.get('q') || '';
    estado.pagina = parseInt(p.get('pagina') || '1', 10) || 1;
    estado.modo = p.get('modo') === 'e' ? 'e' : 'ou';
    estado.ano = (p.get('ano') || '').split(',').filter(Boolean);
    estado.fonte = p.get('fonte') || '';
    estado.origem = p.get('origem') || '';
    estado.modelo = p.get('modelo') === 'tfidf' ? 'tfidf' : 'bm25';
    estado.reforco = p.get('reforco') === '0' ? '0' : '1';
  }

  function paramsAPI() {
    var p = new URLSearchParams();
    p.set('q', estado.q);
    if (estado.pagina > 1) p.set('pagina', estado.pagina);
    if (estado.modo === 'e') p.set('modo', 'e');
    if (estado.ano.length) p.set('ano', estado.ano.join(','));
    if (estado.fonte) p.set('fonte', estado.fonte);
    if (estado.origem) p.set('origem', estado.origem);
    if (estado.modelo !== 'bm25') p.set('modelo', estado.modelo);
    if (estado.reforco === '0') p.set('reforco', '0');
    return p;
  }

  function navegar(mudancas, novaPagina) {
    Object.keys(mudancas || {}).forEach(function (k) { estado[k] = mudancas[k]; });
    if (!novaPagina) estado.pagina = 1;
    history.pushState(null, '', estado.q ? '?' + paramsAPI().toString() : './');
    render();
  }

  // ---------- tela ----------
  function sincronizarControles() {
    document.querySelectorAll('[data-form] input[name=q]').forEach(function (i) { i.value = estado.q; });
    document.querySelectorAll('[data-modo]').forEach(function (b) {
      b.classList.toggle('ativo', b.getAttribute('data-modo') === estado.modo);
    });
    $('#reforco').checked = estado.reforco === '1';
    $('#modelo').value = estado.modelo;
  }

  function render() {
    sincronizarControles();
    document.body.classList.toggle('inicio', !estado.q);
    if (!estado.q) { document.title = 'Radar 2026 · busca de propostas eleitorais'; return; }
    document.title = estado.q + ' · Radar 2026';
    buscar();
  }

  var pedido = 0;
  function buscar() {
    var meu = ++pedido;
    $('#lista').classList.add('carregando');
    fetch('api/buscar?' + paramsAPI().toString())
      .then(function (r) { return r.json(); })
      .then(function (res) {
        if (meu !== pedido) return;          // chegou uma busca mais nova
        $('#lista').classList.remove('carregando');
        if (res.erro) { $('#status').textContent = 'Erro: ' + res.erro; return; }
        mostrar(res);
      })
      .catch(function () {
        $('#lista').classList.remove('carregando');
        $('#status').textContent = 'Não foi possível falar com o servidor. O buscador.py está rodando?';
      });
  }

  function mostrar(res) {
    var s = '<b>' + fmt(res.total) + '</b> documento' + (res.total === 1 ? '' : 's') +
      ' em <b>' + String(res.tempo_busca_ms).replace('.', ',') + ' ms</b>';
    if (res.termos.length) s += ' · termos no índice: ' + res.termos.map(function (t) { return '<code>' + esc(t) + '</code>'; }).join(' ');
    if (res.frases.length) s += ' · frase exata: ' + res.frases.map(function (f) { return '<code>"' + esc(f.join(' ')) + '"</code>'; }).join(' ');
    if (res.excluidos.length) s += ' · sem: ' + res.excluidos.map(function (t) { return '<code>' + esc(t) + '</code>'; }).join(' ');
    $('#status').innerHTML = s;

    var ol = $('#resultados');
    if (!res.resultados.length) {
      var dica = res.termos_ausentes && res.termos_ausentes.length
        ? 'Os termos <b>' + res.termos_ausentes.map(esc).join(', ') + '</b> não aparecem na coleção. '
        : '';
      ol.innerHTML = '<li class="vazio">' + dica + 'Nenhum documento encontrado. Tente menos palavras, o modo "Qualquer palavra" ou tirar os filtros.</li>';
    } else {
      ol.innerHTML = res.resultados.map(function (r) {
        var meta = '<span class="fonte">' + esc(r.fonte) + '</span>';
        if (r.ano) meta += '<span class="etiqueta' + (r.ano === 2026 ? ' ano26' : '') + '">' + r.ano + '</span>';
        meta += '<span class="etiqueta' + (r.origem === 'institucional' ? ' inst' : '') + '">' +
          (r.origem === 'institucional' ? 'Institucional' : 'Mídia') + '</span>';
        return '<li class="res">' +
          '<p class="meta">' + meta + '</p>' +
          '<h2><a href="' + esc(r.url) + '" target="_blank" rel="noopener">' + r.titulo_html + '</a></h2>' +
          '<p class="trecho">' + r.trecho + '</p>' +
          '<div class="acoes"><button type="button" class="link" data-doc="' + r.docid + '">Ler o texto aqui</button>' +
          '<span class="nota" title="Nota final (BM25 antes da reordenação: ' + r.nota_bm25 + ')">#' + r.posicao + ' · nota ' + String(r.nota).replace('.', ',') + '</span></div>' +
          '</li>';
      }).join('');
    }
    facetas(res.facetas || {});
    paginas(res);
    window.scrollTo(0, 0);
  }

  function botaoFaceta(rotulo, n, ativo, acao) {
    return '<button type="button" class="faceta' + (ativo ? ' ativo' : '') + (n ? '' : ' vazia') + '" data-acao="' + esc(acao) + '">' +
      '<span class="caixinha"></span><span class="nome">' + esc(rotulo) + '</span><span class="n">' + fmt(n) + '</span></button>';
  }

  function facetas(f) {
    var anos = (f.ano || []).slice(0, 8);
    estado.ano.forEach(function (a) {   // ano marcado continua visível mesmo sem resultados
      if (!anos.some(function (x) { return String(x[0]) === a; })) anos.push([parseInt(a, 10), 0]);
    });
    $('#f-ano').innerHTML = anos.map(function (x) {
      return botaoFaceta(String(x[0]), x[1], estado.ano.indexOf(String(x[0])) >= 0, 'ano:' + x[0]);
    }).join('') || '<span class="n">—</span>';
    var o = f.origem || {};
    $('#f-origem').innerHTML = botaoFaceta('Mídia', o.midia || 0, estado.origem === 'midia', 'origem:midia') +
      botaoFaceta('Institucional', o.institucional || 0, estado.origem === 'institucional', 'origem:institucional');
    var fontes = (f.fonte || []).slice(0, 10);
    if (estado.fonte && !fontes.some(function (x) { return x[0] === estado.fonte; })) fontes.unshift([estado.fonte, 0]);
    $('#f-fonte').innerHTML = fontes.map(function (x) {
      return botaoFaceta(x[0], x[1], estado.fonte === x[0], 'fonte:' + x[0]);
    }).join('') || '<span class="n">—</span>';
    $('#limpar').hidden = !(estado.ano.length || estado.fonte || estado.origem);
    var n = estado.ano.length + (estado.fonte ? 1 : 0) + (estado.origem ? 1 : 0);
    $('#abrir-filtros').textContent = n ? 'Filtros (' + n + ')' : 'Filtros';
    $('#abrir-filtros').classList.toggle('ativo', n > 0);
  }

  function paginas(res) {
    var total = Math.min(Math.ceil(res.total / res.por_pagina), 20);
    var nav = $('#paginas');
    if (total <= 1) { nav.innerHTML = ''; return; }
    var p = res.pagina, h = '';
    h += '<button type="button" data-pag="' + (p - 1) + '"' + (p <= 1 ? ' disabled' : '') + '>‹</button>';
    for (var i = 1; i <= total; i++) {
      if (i === 1 || i === total || Math.abs(i - p) <= 2) {
        h += '<button type="button" data-pag="' + i + '"' + (i === p ? ' class="ativo"' : '') + '>' + i + '</button>';
      } else if (Math.abs(i - p) === 3) {
        h += '<button type="button" disabled>…</button>';
      }
    }
    h += '<button type="button" data-pag="' + (p + 1) + '"' + (p >= total ? ' disabled' : '') + '>›</button>';
    nav.innerHTML = h;
  }

  // ---------- leitor ----------
  function abrirDocumento(id) {
    fetch('api/documento?id=' + id + '&q=' + encodeURIComponent(estado.q))
      .then(function (r) { return r.json(); })
      .then(function (d) {
        $('#l-meta').textContent = [d.fonte, d.ano, d.origem === 'institucional' ? 'Institucional' : 'Mídia'].filter(Boolean).join(' · ');
        $('#l-titulo').textContent = d.titulo;
        $('#l-url').href = d.url;
        $('#l-texto').innerHTML = d.paragrafos.map(function (p) { return '<p>' + p + '</p>'; }).join('');
        $('#veu').hidden = false;
        $('#leitor').hidden = false;
        $('#leitor').scrollTop = 0;
        var m = $('#l-texto mark');
        if (m) m.scrollIntoView({ block: 'center' });
      });
  }
  function fecharDocumento() { $('#veu').hidden = true; $('#leitor').hidden = true; }

  // ---------- eventos ----------
  document.querySelectorAll('[data-form]').forEach(function (f) {
    f.addEventListener('submit', function (e) {
      e.preventDefault();
      var q = f.querySelector('input[name=q]').value.trim();
      if (q) navegar({ q: q });
    });
  });
  document.querySelectorAll('[data-ex]').forEach(function (b) {
    b.addEventListener('click', function () { navegar({ q: b.getAttribute('data-ex') }); });
  });
  document.querySelectorAll('[data-modo]').forEach(function (b) {
    b.addEventListener('click', function () { navegar({ modo: b.getAttribute('data-modo') }); });
  });
  $('#reforco').addEventListener('change', function (e) { navegar({ reforco: e.target.checked ? '1' : '0' }); });
  $('#modelo').addEventListener('change', function (e) { navegar({ modelo: e.target.value }); });
  $('[data-home]').addEventListener('click', function (e) { e.preventDefault(); navegar({ q: '' }); });
  $('#limpar').addEventListener('click', function () { navegar({ ano: [], fonte: '', origem: '' }); });

  $('#filtros').addEventListener('click', function (e) {
    var b = e.target.closest('[data-acao]');
    if (!b) return;
    var a = b.getAttribute('data-acao'), i = a.indexOf(':'), tipo = a.slice(0, i), valor = a.slice(i + 1);
    if (tipo === 'ano') {
      var anos = estado.ano.slice(), j = anos.indexOf(valor);
      if (j >= 0) anos.splice(j, 1); else anos.push(valor);
      navegar({ ano: anos });
    } else if (tipo === 'origem') {
      navegar({ origem: estado.origem === valor ? '' : valor });
    } else if (tipo === 'fonte') {
      navegar({ fonte: estado.fonte === valor ? '' : valor });
    }
  });
  $('#paginas').addEventListener('click', function (e) {
    var b = e.target.closest('[data-pag]');
    if (b && !b.disabled) { estado.pagina = parseInt(b.getAttribute('data-pag'), 10); navegar({}, true); }
  });
  $('#resultados').addEventListener('click', function (e) {
    var b = e.target.closest('[data-doc]');
    if (b) abrirDocumento(b.getAttribute('data-doc'));
  });
  $('#abrir-filtros').addEventListener('click', function () { $('#filtros').classList.toggle('aberto'); });
  $('#veu').addEventListener('click', fecharDocumento);
  $('#leitor .fechar').addEventListener('click', fecharDocumento);
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') fecharDocumento(); });
  window.addEventListener('popstate', function () { lerURL(); render(); });

  // números da coleção na tela inicial
  fetch('api/info').then(function (r) { return r.json(); }).then(function (i) {
    info = i;
    $('#numeros').innerHTML = '<b>' + fmt(i.documentos) + '</b> documentos · <b>' + fmt(i.termos) +
      '</b> termos no índice · <b>' + fmt(i.origem.institucional) + '</b> páginas institucionais de candidatos e partidos';
  });

  lerURL();
  render();
})();
