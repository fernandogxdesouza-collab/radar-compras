# Radar de Compras

Robô que consulta preços, guarda o histórico e avisa quando um item chega ao mínimo. Mesmo modelo do radar de passagens.

## Como funciona
- `produtos.json`: lista de produtos (busca, o que exigir e excluir no título, unidade para preço por kg/litro, preço-alvo opcional).
- `beneficios.json`: o que você tem (portais de cashback, Pix, cartão, clubes). Só `ativo: true` entra na conta. O Zoom já vem ligado, com as taxas lidas do site.
- `radar.py`: coleta, lê a página do produto no Zoom (40 dias de histórico diário, termômetro e todas as lojas), calcula o melhor caminho (portal + pagamento), grava `dados/` e gera `docs/index.html`.
- **Termômetro:** baixo, normal ou alto, pela posição do preço de hoje nos últimos 40 dias (baixo = no quartil inferior e 3% abaixo da mediana). Se o Zoom marcar `BEST_HISTORY_PRICE`, vale como baixo. Produtos sem página de produto no Zoom (café, por exemplo) usam o histórico próprio, que precisa de 7 dias.
- **Quer pagar mais barato:** o ntfy avisa na faixa baixa ou abaixo do `preco_alvo`, já com o melhor caminho e o clube. Não repete até o preço cair mais 2% ou passarem 3 dias.
- **Estratégias:** um portal por compra; pagamento com `empilha_portal: false` anula o portal. A taxa do Zoom só vale com cashback ativado e entrando pelo botão da oferta.

## Fontes
| Fonte | Situação em 16/11/2026 |
|---|---|
| Zoom (também Buscapé e Bondfaro, mesma base) | Funciona. Traz Magalu, Amazon, KaBuM! e outras lojas, mais o cashback do Zoom. |
| Amazon direto | Responde 503 ao robô (proteção anti-robô). Fica desligada na coleta; a Amazon aparece via Zoom. |
| Mercado Livre | Bloqueado (exige verificação de conta). Use `manuais` em `produtos.json` para lançar preços que você viu (valem 14 dias), ou a API oficial com credenciais próprias. |
| Méliuz (só taxas de cashback) | Funciona: lê a página pública de cada loja, com a tabela por categoria (Magalu: 8% eletrodomésticos, 5% geral, 2% demais). Roda todo dia; grava `dados/meliuz.json`. |
| Magalu direto, Shopee, Jacotei | Bloqueados (403). |
| Supermercados BH, Mart Minas, Paranaíba | Sem preço online nas páginas de busca. Entram na fase 2 (encartes). |

Não contornamos proteção anti-robô. Se uma fonte passar a oferecer API ou feed oficial, entra em `FONTES` no `radar.py`.

## Rodar local
```bash
pip install -r requirements.txt
RADAR_SEM_ALERTA=1 python radar.py            # todos os produtos
RADAR_SEM_ALERTA=1 python radar.py azeite     # só um
```

## Pesquisar qualquer produto e editar a lista (pelo painel)
O painel tem caixa de busca, "Incluir na lista", "Excluir da lista" e "Definir alvo". Cada botão dispara o workflow **Comando do painel** (`comando.yml` + `comando.py`) no GitHub.
- **Pesquisar:** resultado completo (termômetro, melhor caminho, ofertas) em 1 a 3 minutos, em `docs/buscas/` (guarda as 30 últimas). Não entra na lista nem avisa.
- **Item de supermercado:** se a pesquisa for por peso/volume (ex.: arroz 5kg) ou casar com algum folheto, o resultado ganha "Comparação com os encartes locais" (melhor item parecido de cada rede, % contra o melhor preço online) e "Conferir nas lojas que o robô não lê" (links prontos de pesquisa em Mercado Livre, Shopee, Amazon, Carrefour, Pão de Açúcar etc.). O robô só lê preços pelo Zoom; as demais lojas bloqueiam e não são contornadas.
- **Incluir** (`texto; alvo=100` opcional): acrescenta em `produtos.json` com filtro automático pelas palavras do texto (tamanhos como 5kg viram unidade de preço). Aparece na lista em cerca de 6 minutos.
- **Excluir / Alvo:** por id ou parte do nome.
- **Conectar uma vez por aparelho:** crie um token fine-grained só para este repositório com **Actions: Read and write** (o painel explica o passo a passo) e cole no painel; fica só no navegador. Sem token, os botões abrem a página do workflow para rodar à mão.
- Também dá para editar `produtos.json` direto ou pedir ao Claude.

## Aviso de supermercado muito abaixo do normal
`dados/ofertas_super.json` guarda todos os itens dos folhetos (de/por, validade, clube). `avisos_super.py` roda no GitHub quando esse arquivo é enviado e avisa no ntfy os itens com desconto sobre o preço "de" do folheto acima de `super.desconto_minimo` (padrão 30%), estejam ou não na lista; não repete o mesmo item. A extração é dividida em duas tarefas, ambas segunda e quinta, e cada uma troca só os itens da sua rede:
- **Rotina na nuvem `radar-compras-folhetos`** (09h07 Brasília, roda sem o PC; ambiente de nuvem `radar-folhetos`, rede limitada a `www.martminas.com.br`, `supermercadosparanaiba.com.br`, `superbh.app`, `*.superbh.app` e `stgrsbhprd.blob.core.windows.net`): lê Mart Minas (PDF) e Paranaíba (imagens).
- **Tarefa do PC `radar-compras-folhetos`** (09h37, precisa do app Claude aberto): lê só o Supermercados BH. O servidor do BH (`superbh.app`) encerra conexões vindas da nuvem do Claude e do GitHub ("connection reset"), mas responde do PC de casa, então o BH não pode ir para a nuvem. Itens sem preço "de" no folheto não têm base de comparação e não disparam aviso.

## Supermercados (Matozinhos e Pedro Leopoldo)
Os três publicam folheto quinzenal, mas sem preço consultável por produto, então entram por `manuais` com `fisico: true` e `validade` do folheto. Atualize a cada folheto novo.
- **Mart Minas:** não tem loja em Matozinhos; a de Pedro Leopoldo está em `martminas.com.br/loja/pedro-leopoldo/` (botão "Faça download do arquivo", PDF com texto legível). Clube: Mart Mais (só cadastrados).
- **Supermercados BH:** `supermercadosbh.com.br/folhetos`, cidade Matozinhos; o PDF só aparece com o navegador. Clube: app Meu BH e Cartão BH.
- **Paranaíba:** `supermercadosparanaiba.com.br/ofertas/` publica o jornal como imagens PNG (ler a olho ou OCR). Lojas em Matozinhos (Av. Caio Martins, 245) e Pedro Leopoldo. Clube: app Clube Paranaíba (itens "Clube de Desconto").
- Em compra presencial valem só formas de pagamento marcadas `presencial` em `beneficios.json` (hoje o cartão Nubank). Portal de cashback, Pix e frete não se aplicam.

## Ligar o Mercado Livre (API oficial)
1. Entre em developers.mercadolivre.com.br com sua conta, abra "Minhas aplicações" e crie um app (nome livre; leia os termos e aceite como dono do app). Anote o Client ID e o Client Secret.
2. Local: defina as variáveis `ML_CLIENT_ID` e `ML_CLIENT_SECRET` no terminal antes de rodar `python radar.py`. Nuvem: crie os secrets de mesmo nome no repositório.
3. Rode e veja a linha `fontes:` de cada produto: `mercadolivre` deve mostrar um número maior que 0. Se aparecer "recusado (HTTP 403)", o app não tem permissão de busca e o Mercado Livre segue só pelo modo `manuais`.
Nunca cole o Client Secret em arquivo do projeto nem no chat.

## Publicar (falta fazer)
1. Criar o repositório no GitHub e enviar esta pasta.
2. Secret `NTFY_TOPICO` com um tópico novo do ntfy (diferente do radar de passagens).
3. Ativar GitHub Pages em `main` / pasta `docs`.
4. Rodar o workflow uma vez em Actions e conferir se o Zoom responde a partir dos servidores do GitHub (IP de datacenter pode ser bloqueado; em caso de bloqueio, rodar no PC pelo Agendador de Tarefas).

## Limites conhecidos
- O Zoom devolve só a melhor oferta de cada produto; as outras lojas aparecem em `outras_lojas` do CSV/estado, sem preço.
- Itens de supermercado dependem do que o Zoom lista; arroz tem poucos resultados.
- "Frigideira Redstone" é a Philco PPH240AFR (revestimento Redstone), não fritadeira elétrica.
