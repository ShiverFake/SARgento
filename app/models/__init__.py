"""
MODEL (o "M" do MVC)

Aqui fica tudo que é DADO e REGRA DE NEGÓCIO:
- barragem.py      -> ler o CSV e os polígonos
- database.py      -> salvar/ler a série temporal no SQLite
- geo.py           -> utilidades geográficas (projeções, rasterização)
- sentinel1.py     -> buscar e carregar cenas Sentinel-1
- processamento.py -> dB, filtro de Lee, Otsu, máscara e áreas
- anomalias.py     -> mediana móvel + MAD e variação percentual
- sentinel2.py     -> validação opcional com Sentinel-2

Os models NÃO sabem nada de Flask nem de HTML.
"""
