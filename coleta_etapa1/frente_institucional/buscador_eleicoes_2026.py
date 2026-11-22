#!/usr/bin/env python
# -*- encoding: utf-8 -*-
# Created on 2026-09-25
# Project: buscador_eleicoes_2026

from pyspider.libs.base_handler import *
import re

class Handler(BaseHandler):
    crawl_config = {
    }

    @every(minutes=24 * 60)
    def on_start(self):
        # User-Agent de navegador: alguns portais gov.br e jus.br recusam o User-Agent padrão do pyspider
        meu_disfarce = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
        
        seeds = [
            # Institucionais e Partidos
            'https://www.camara.leg.br/noticias/',
            'https://www12.senado.leg.br/noticias/',
            'https://www.almg.gov.br/atividade-parlamentar/projetos-de-lei/',
            'https://pt.org.br/resolucoes-e-documentos/',
            'https://pl22.com.br/noticias/',
            'https://mdb.org.br/noticias/',
            'https://psd.org.br/noticias/',
            'https://uniaobrasil.org.br/noticias/',
            'https://psol50.org.br/manifestos/',
            'https://pdt.org.br/noticias/',
            'https://www.tse.jus.br/hotsites/catalogo-publicacoes/',
            
            # Portais de análise política com alto volume
            'https://www.gazetadopovo.com.br/eleicoes/',
            'https://www.cartacapital.com.br/politica/',
            'https://congressoemfoco.uol.com.br/',
            'https://valor.globo.com/politica/',
            'https://www.politize.com.br/artigos/',
            'https://www.gov.br/planalto/pt-br/acompanhe-o-planalto/noticias',
            'https://www.alerj.rj.gov.br/Noticias/',
            'https://www.al.sp.gov.br/noticias/',
            'https://www.al.ce.gov.br/noticias',
            'https://www.assembleia.pr.leg.br/comunicacao/noticias',
            'https://www.psb40.org.br/noticias/',
            'https://republicanos10.org.br/noticias/',
            
            # Novos partidos
            'https://podemos.org.br/noticias/',
            'https://solidariedade.org.br/noticias/',
            'https://pcdob.org.br/noticias/',
            'https://cidadania23.org.br/noticias/',
            
            # Mais Assembleias Legislativas estaduais de alto volume
            'https://www.al.ba.gov.br/noticias',
            'https://www.al.rs.gov.br/noticias/',
            'https://www.al.mt.gov.br/noticias/',
            'https://www.al.pe.leg.br/noticias/',
            
            # Cortes Superiores (Decisões políticas e eleitorais)
            'https://noticias.stf.jus.br/',
            'https://www.stj.jus.br/sites/portalp/Noticias',

            # Partidos Políticos Adicionais
            'https://avante70.org.br/noticias/',
            'https://rede.org.br/noticias/',
            'https://pmb.org.br/noticias/',
            'https://up80.org.br/noticias/',
            'https://agirdirigentes.com.br/noticias/',
            'https://dc.org.br/noticias/',
            'https://prtb.org.br/noticias/',
            'https://pco.org.br/noticias/',
            'https://pstu.org.br/noticias/',

            # Câmaras Municipais das Capitais (Projetos e Notícias locais)
            'https://www.saopaulo.sp.leg.br/noticias/',
            'https://www.camara.rj.gov.br/noticias',
            'https://www.cmbh.mg.gov.br/noticias',
            'https://www.curitiba.pr.leg.br/informacao/noticias',
            'https://www.camarapoa.rs.gov.br/noticias',
            'https://www.camarassalvador.ba.gov.br/noticias',
            'https://www.recife.pe.leg.br/noticias',
            'https://www.cmfor.ce.gov.br/noticias',
            'https://www.camaramanaus.am.gov.br/noticias',
            'https://www.camaragoiania.go.gov.br/noticias',

            # Assembleias Legislativas de todo o país
            'https://www.al.ac.leg.br/noticias',
            'https://www.al.al.leg.br/noticias',
            'https://www.aleam.gov.br/noticias',
            'https://www.cl.df.leg.br/noticias',
            'https://www.al.es.gov.br/noticias',
            'https://www.al.go.leg.br/noticias',
            'https://www.al.ma.leg.br/noticias',
            'https://www.al.ms.leg.br/noticias',
            'https://www.al.pa.leg.br/noticias',
            'https://www.al.pb.leg.br/noticias',
            'https://www.al.pi.leg.br/noticias',
            'https://www.al.rn.leg.br/noticias',
            'https://www.al.ro.leg.br/noticias',
            'https://www.al.rr.leg.br/noticias',
            'https://www.al.sc.leg.br/noticias',
            'https://www.al.se.leg.br/noticias',
            'https://www.al.to.leg.br/noticias',
            
            # Fundações Partidárias e Institutos
            'https://fundacaoulysses.org.br/noticias/',
            'https://www.institutomillenium.org.br/artigos/',
            'https://transparencia.org.br/noticias',
            
            # Agências de Notícias (Alta estabilidade e volume)
            'https://agenciabrasil.ebc.com.br/politica',
            'https://agenciabrasil.ebc.com.br/justica',
            'https://www12.senado.leg.br/noticias/agencia-senado',
            'https://www.camara.leg.br/noticias/agencia-camara/',
            'https://agencia.ac.gov.br/noticias/politica/',
            'https://www.agenciaminas.mg.gov.br/noticias/politica',
            'https://agenciapara.com.br/noticias/politica',
            'https://www.aen.pr.gov.br/Noticias',
            
            # Sites especializados em Leis e Concursos
            'https://www.direitocom.com/noticias',
            'https://www.grancursosonline.com.br/noticias/noticias-concursos'
        ]
        
        for url in seeds:
            self.crawl(url, callback=self.index_page, validate_cert=False, headers=meu_disfarce)

    @config(age=10 * 24 * 60 * 60)
    def index_page(self, response):
        if not response.ok:
            return
            
        meu_disfarce = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
        
        for each in response.doc('a[href^="http"]').items():
            url = each.attr.href
            
            # Filtro reforçado: ignora arquivos, imagens, tags e categorias vazias
            if url.lower().endswith(('.pdf', '.doc', '.docx', '.xls', '.xlsx', '.zip', '.rar', '.jpg', '.png', '.jpeg')):
                continue
            if '/wp-content/uploads/' in url.lower() or '/tag/' in url.lower() or '/category/' in url.lower():
                continue
                
            if re.search(r'(/202[3-6]/|/noticia|/artigo|/politica|/eleicao|/poder|/brasil|proposta|projeto|documento|resolucao)', url.lower()):
                self.crawl(url, callback=self.detail_page, validate_cert=False, headers=meu_disfarce)
                
        for each in response.doc('a[href^="http"]').items():
            url = each.attr.href
            
            if re.search(r'(page|pagina|p)[/=?]\d+', url.lower()) or each.text().strip().lower() in ['próxima', 'next', 'mais', 'avançar']:
                match = re.search(r'(?:page|pagina|p)[/=?](\d+)', url.lower())
                if match:
                    if int(match.group(1)) > 800:
                        continue 
                self.crawl(url, callback=self.index_page, validate_cert=False, headers=meu_disfarce)

    @config(priority=2)
    def detail_page(self, response):
        if not response.ok or not response.doc:
            return
            
        title = response.doc('h1').text()
        
        paragraphs = [p.text() for p in response.doc('article p, .text p, .content p, p').items() if p.text()]
        full_text = "\n".join(paragraphs)

        if full_text and len(full_text) > 200:
            return {
                "url": response.url,
                "title": title,
                "text": full_text,
                "domain": response.doc('title').text()
            }