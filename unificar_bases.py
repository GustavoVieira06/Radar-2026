import pandas as pd
import glob
import json
import gzip

# 1. Carregar a sua base de dados
print("Lendo sua coleta...")
df_meu = pd.read_json('minha_coleta.json', lines=True)

# 2. Carregar a base do colega (arquivos compactados)
print("Lendo a coleta do colega...")
arquivos_colega = glob.glob('*.gz')

dados_colega = []
for arquivo in arquivos_colega:
    with gzip.open(arquivo, 'rt', encoding='utf-8') as f:
        for linha in f:
            try:
                dados_colega.append(json.loads(linha))
            except:
                continue

df_colega = pd.DataFrame(dados_colega)

# 3. Unificar as duas bases
print("Juntando e removendo duplicatas...")
df_final = pd.concat([df_meu, df_colega], ignore_index=True)

# 4. Remover links repetidos
if 'url' in df_final.columns:
    df_final = df_final.drop_duplicates(subset=['url'])

# 5. Salvar o arquivo CSV final
df_final.to_csv('corpus_eleicoes_2026_final.csv', index=False, encoding='utf-8-sig')
print(f"Processo concluído! Arquivo final gerado com {len(df_final)} documentos.")