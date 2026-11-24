# Painéis de conferência visual

Imagens geradas durante a validação do monitoramento (Sentinel-2 L2A e
Sentinel-1 RTC). Em cada painel de três quadros: **RGB** com o polígono
(amarelo) e a crista (magenta) · **máscara S2** (MNDWI) · **máscara S1** da
passagem mais próxima.

| Arquivo | O que mostra |
|---|---|
| `geometria_poligonos_e_cristas.png` | Polígonos aprovados (Eustáquio, Santo Antônio, lago oeste) e trechos de crista, sobre S2 de 20/10/2026 |
| `geometria_eustaquio_margem_oeste.png` | Margem de 40 m na borda oeste da Eustáquio (sem vegetação) |
| `geometria_lago_oeste.png` | Contorno do lago oeste traçado pela água (MNDWI > 0,2) |
| `eustaquio_calibracao_mndwi_4_datas.png` | "Lago visível" no RGB (contorno ciano) usado para calibrar o limiar MNDWI = 0,25 |
| `eustaquio_2025-07-27_referencia_seca.png` | Referência de seca: o S1 marca também a praia saturada; o S2 marca só o lago |
| `eustaquio_2025-12-29_referencia_chuva.png` | Referência de chuva |
| `eustaquio_2025-11-14_reprovada_qc_scl.png` | Data reprovada no controle de qualidade (SCL 3% e MNDWI 56% do núcleo): parte do lago "apagada" |
| `eustaquio_qc_scl_2025.png`, `eustaquio_qc_scl_2026.png` | Água segundo a SCL (azul) em datas aprovadas e reprovadas pela regra só-SCL; motivo da regra combinada SCL + MNDWI |
| `eustaquio_2026-08-18_alerta_aumento.png` | Alerta de 18/08/2026: aumento do lago (509 → 553 ha), cena limpa |
| `santo-antonio_2026-05-18_antes_do_evento.png` | Poça no canto sudeste visível no S1 e no S2 (15,7 ha) |
| `santo-antonio_2026-06-07_alerta_reducao.png` | Mesma área sem água no S1 e no S2 (0,6 ha) |
| `santo-antonio_2026-09-20_praia_seca_lisa.png` | Praia branca seca aparece escura no S1 (limitação demonstrada) |
| `lago-oeste_2025-07-02_bacia_vazia.png` | Bacia do lago oeste sem lâmina d'água antes do enchimento de jan/2026 |

Para gerar um painel de qualquer data:

```bash
python main.py referencias --barragem "Eustáquio" --data 2026-08-18
```
