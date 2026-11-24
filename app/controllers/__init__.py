"""
CONTROLLER (o "C" do MVC)

Os controllers são os "maestros": recebem um pedido (do terminal ou do
navegador), chamam os models na ordem certa e entregam o resultado para a
view (HTML/JSON).

- pipeline_controller.py  -> processa as imagens (terminal)
- relatorio_controller.py -> gera relatórios HTML em reports/
- web_controller.py       -> rotas do site Flask
"""
