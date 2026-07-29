# Formulação matemática 2 — VRPP + Lookahead com jornada de trabalho operacional

Especificação completa do modelo implementado em
[src/vrpp_lookahead/vrpp.py](src/vrpp_lookahead/vrpp.py),
[src/vrpp_lookahead/lookahead.py](src/vrpp_lookahead/lookahead.py),
[src/vrpp_lookahead/config.py](src/vrpp_lookahead/config.py) e
[src/vrpp_lookahead/instance.py](src/vrpp_lookahead/instance.py).

*Versão em português europeu de [formulation2.md](formulation2.md). Os dois
ficheiros descrevem o mesmo modelo; em caso de divergência, a versão inglesa é a
de referência.*

**O que este documento acrescenta face a [formulation.md](formulation.md).** A
primeira formulação preça a distância e nunca o tempo. Mede o tempo de viagem,
reporta-o, e não restringe nada — pelo que nada a impede de devolver uma rota
que nenhuma equipa consegue cumprir. Esta versão fecha essa lacuna: introduz um
tempo de serviço por contentor, uma velocidade média de volta e um limite rígido
à duração da jornada de trabalho, tudo como restrições dentro do MILP e não como
verificações aplicadas a posteriori. As secções
[3.4](#34-parâmetros-da-jornada-de-trabalho),
[4.2](#42-tempo-de-travessia-de-um-arco), [6](#6-variáveis-de-decisão),
[8.10–8.13](#810-turno--ativação-de-arco) e
[10](#10-o-que-a-jornada-de-trabalho-altera) são novas ou substancialmente
reescritas; o resto foi transposto para que este ficheiro se sustente sozinho.

Este documento descreve o que o código resolve de facto, não uma versão
idealizada dele. Cada equação remete para as linhas que a constroem, e a
[secção 12](#12-o-que-o-modelo-continua-a-não-captar) enuncia claramente o que
continua de fora.

---

## Índice

1. [Enunciado do problema](#1-enunciado-do-problema)
2. [Conjuntos e índices](#2-conjuntos-e-índices)
3. [Dados de entrada e parâmetros](#3-dados-de-entrada-e-parâmetros)
4. [Grandezas derivadas](#4-grandezas-derivadas)
5. [Classificação lookahead](#5-classificação-lookahead)
6. [Variáveis de decisão](#6-variáveis-de-decisão)
7. [Função objetivo](#7-função-objetivo)
8. [Restrições](#8-restrições)
9. [Pré-processamento](#9-pré-processamento)
10. [O que a jornada de trabalho altera](#10-o-que-a-jornada-de-trabalho-altera)
11. [Simulação multi-dia](#11-simulação-multi-dia)
12. [O que o modelo continua a não captar](#12-o-que-o-modelo-continua-a-não-captar)
13. [Dimensão do modelo e método de resolução](#13-dimensão-do-modelo-e-método-de-resolução)
14. [Mapa equação-código](#14-mapa-equação-código)

---

## 1. Enunciado do problema

Uma frota de veículos idênticos, sediada num único depósito, recolhe resíduos de
um conjunto de contentores dispersos por uma rede rodoviária. Ao contrário de um
VRP clássico, **não é obrigatório visitar todos os contentores**: cada um
transporta um *lucro* proporcional aos resíduos que contém, e o operador é livre
de ignorar um contentor cuja recolha não compense o desvio. É o **Problema de
Encaminhamento de Veículos com Lucros (VRPP)**, na variante *prize-collecting* —
a função objetivo troca receita contra custo de deslocação, em vez de minimizar
distância sob restrições de cobertura total.

Três aspetos tornam esta instância específica:

* **Subconjunto obrigatório.** Os contentores que transbordariam antes da visita
  seguinte são forçados na solução independentemente da rentabilidade. Resultam
  da classificação *lookahead* da [secção 5](#5-classificação-lookahead).
* **Decisão sobre a dimensão da frota.** O número de rotas efetivamente usadas,
  `k`, é ele próprio uma variável limitada por `MAX_ROUTES`, e cada veículo
  utilizado custa um valor fixo `OMEGA`.
* **Uma jornada de trabalho limitada.** Cada rota tem de ser cumprível por uma
  equipa dentro de um turno de `max_shift_h` horas, contando tanto a condução
  como o tempo gasto a esvaziar contentores. É isto que separa a formulação 2 da
  formulação 1.

O resultado é um único programa linear inteiro misto resolvido com o Gurobi, um
por dia simulado.

### Porque é que a jornada é uma restrição e não um relatório

Na formulação 1 uma paragem é gratuita. Servir um contentor custa apenas o
desvio necessário para lá chegar, pelo que, assim que os contentores estão
densos — como estão num aglomerado urbano —, o modelo acrescenta paragens quase
sem limite até esgotar a capacidade do veículo. Na instância de referência isso
produziu duas rotas de **9,58 h e 8,03 h**: rentáveis no papel, incumpríveis na
prática.

Duas omissões distintas o causaram, e ambas são corrigidas aqui:

1. **Ausência de tempo de serviço.** Esvaziar um contentor consome à equipa um
   tempo fixo, independente da distância. Sem ele, 300 paragens custam o mesmo
   que 100.
2. **Ausência de limite de duração.** Nada limitava a soma de condução e
   serviço.

Acrescentar a primeira sem a segunda apenas mudaria a contabilidade.
Acrescentar as duas altera qual é a solução ótima — que é o objetivo.

---

## 2. Conjuntos e índices

| Símbolo | Definição |
|---|---|
| $N = \{0, 1, \dots, n\}$ | todos os nós; o índice $0$ é o depósito |
| $N_R = N \setminus \{0\}$ | pontos de recolha (contentores), $\lvert N_R \rvert = n$ |
| $A \subseteq N \times N$ | conjunto de arcos após pré-filtragem, $i \neq j$ |
| $\mathcal{MG} \subseteq N_R$ | contentores MustGo — transbordam amanhã |
| $\mathcal{LA} \subseteq N_R$ | contentores MustGo-LookAhead — transbordam dentro do horizonte |
| $\mathcal{F} = \mathcal{MG} \cup \mathcal{LA}$ | contentores forçados, após o ajuste de capacidade da [secção 9.2](#92-ajuste-dos-mustgo-à-frota-disponível) |

O nó $0$ é o depósito, simultaneamente como origem e como destino: cada rota é
um percurso fechado $0 \rightarrow \cdots \rightarrow 0$.

O conjunto de arcos $A$ **não** é o dígrafo completo. É reduzido pela filtragem
da [secção 9.1](#91-filtragem-de-arcos), que é o que torna tratável uma
instância de ~500 nós.

---

## 3. Dados de entrada e parâmetros

### 3.1 Dados por contentor, lidos do ficheiro de atributos

| Símbolo | Coluna | Unidade | Significado |
|---|---|---|---|
| $\mathrm{Ncont}_i$ | `Ncont` | — | número de contentores instalados no ponto $i$ |
| $\mathrm{Vol}_i$ | `Vol_cont` | m³ | volume de **um** contentor no ponto $i$ |
| $\mathrm{Vkg}_i$ | `Vol_kg` | kg | resíduos atualmente contidos no ponto $i$ |
| $a_i$ | `ai` | %/dia | taxa de enchimento diária, em percentagem da capacidade do ponto |

### 3.2 Dados da rede, da matriz OpenRouteService

| Símbolo | Unidade | Significado |
|---|---|---|
| $D_{ij}$ | km | distância rodoviária de $i$ para $j$ |
| $T_{ij}$ | min | tempo de percurso rodoviário de $i$ para $j$ |

$D$ é **assimétrica** em geral ($D_{ij} \neq D_{ji}$), por provir de uma rede
rodoviária real e orientada, com sentidos únicos. A formulação é enunciada sobre
um grafo dirigido exatamente por essa razão.

### 3.3 Parâmetros económicos e de frota, do bloco `model:` do YAML

| Símbolo | Chave | Unidade | Significado |
|---|---|---|---|
| $B$ | `B` | kg/m³ | densidade dos resíduos — converte volume em peso |
| $Q$ | `Q` | kg | capacidade do veículo |
| $R$ | `R` | €/kg | receita por kg recolhido |
| $C$ | `C` | €/km | custo de deslocação |
| $\Omega$ | `OMEGA` | € | custo fixo por veículo utilizado |
| $K^{\max}$ | `MAX_ROUTES` | — | número máximo de rotas |
| $\theta^{MG}$ | `threshold_mg` | % | limiar MustGo |
| $\theta^{OVF}$ | `threshold_overflow` | % | limiar de transbordo do lookahead |
| $W$ | `window` | dias | horizonte do lookahead |

### 3.4 Parâmetros da jornada de trabalho, do bloco `shift:` do YAML

Novos nesta formulação. Definidos na classe `Shift`
([config.py](src/vrpp_lookahead/config.py)).

| Símbolo | Chave | Unidade | Significado |
|---|---|---|---|
| $v$ | `speed_kmh` | km/h | velocidade média da volta de recolha |
| $s$ | `service_time_min` | min | tempo gasto a esvaziar os contentores de um ponto |
| $H$ | `max_shift_h` | h | duração máxima da jornada de uma equipa |
| — | `enforce` | booleano | `true` impõe o turno como restrição rígida; `false` apenas o reporta |
| — | `report_h` | h | lista de durações de turno reportadas como cabe / excede |

Estas cinco chaves incorporam três decisões de modelação, e cada uma merece ser
defendida explicitamente.

**Porque é que $v$ substitui os tempos ORS $T_{ij}$.** O ORS devolve tempos de
condução em fluxo livre para o perfil de veículo. Uma volta de recolha não os
atinge: para de poucas em poucas centenas de metros, manobra um veículo pesado
junto ao contentor e volta a arrancar. Na instância de referência os tempos ORS
implicam cerca de 34 km/h, valor otimista para este ciclo de trabalho. Uma
velocidade média única é um pressuposto que o operador pode enunciar, defender e
calibrar contra os seus próprios registos — um tempo por arco vindo de uma API
de encaminhamento não se calibra da mesma maneira. Em consequência, $T_{ij}$
sobrevive nos relatórios como valor de referência, mas não intervém na
restrição.

**Porque é que $s$ é constante entre contentores.** O tempo de serviço varia
realisticamente com o número de contentores $\mathrm{Ncont}_i$ instalados no
ponto. Tornar $s$ proporcional a $\mathrm{Ncont}_i$ é uma alteração de uma linha
na [equação 8.12](#812-turno--propagação-do-tempo) e não altera a estrutura do
modelo. Usa-se uma constante porque o operador fornece um valor, não uma
decomposição por ponto, e inventar essa decomposição daria falsa precisão.

**Porque é que `enforce` é um interruptor e não permanente.** Com
`enforce: false` o modelo é exatamente a formulação 1 e reproduz os resultados
publicados, enquanto os relatórios continuam a trazer as colunas da jornada.
Isto mantém um único caminho de código para as duas formulações e torna o efeito
da restrição mensurável mudando uma só chave — ver a
[secção 10](#10-o-que-a-jornada-de-trabalho-altera).

---

## 4. Grandezas derivadas

### 4.1 Grandezas por contentor

Calculadas uma vez em [instance.py](src/vrpp_lookahead/instance.py) e usadas em
tudo o resto:

$$
\mathrm{CAP}_i \;=\; B \cdot \mathrm{Ncont}_i \cdot \mathrm{Vol}_i
\qquad \text{[kg]}
$$

$$
S_i \;=\; \mathrm{Vkg}_i
\qquad \text{[kg]}
$$

$$
\alpha_i \;=\; \frac{a_i}{100} \cdot \mathrm{CAP}_i
\qquad \text{[kg/dia]}
$$

* $\mathrm{CAP}_i$ — capacidade total do ponto $i$ em quilogramas. Um ponto pode
  conter vários contentores, daí o fator $\mathrm{Ncont}_i$. A densidade $B$ é o
  que converte o volume do contentor num peso.
* $S_i$ — resíduos existentes no ponto $i$ no início do dia. É esta a grandeza
  que gera receita se o ponto for servido, pelo que desempenha o papel de
  *prémio* no VRPP. Note-se que $S_0 = 0$ para o depósito.
* $\alpha_i$ — quilogramas acumulados por dia no ponto $i$.

Um ponto degenerado com $\mathrm{Ncont}_i = 0$ ou $\mathrm{Vol}_i = 0$ daria
$\mathrm{CAP}_i = 0$ e tornaria indefinido o rácio de enchimento; nesse caso o
carregador rejeita a instância.

### 4.2 Tempo de travessia de um arco

Novo nesta formulação. Para cada arco do conjunto filtrado:

$$
\tau_{ij} \;=\; \frac{D_{ij}}{v} \cdot 60
\qquad \text{[min]}, \qquad (i,j) \in A
$$

e o limite de turno é transportado em minutos, para coincidir:

$$
H^{\min} \;=\; 60\,H
\qquad \text{[min]}
$$

O custo total em tempo de percorrer $(i,j)$ e servir $j$ é $\tau_{ij} + s$. O
método auxiliar `Shift.route_min(km, n)` em
[config.py](src/vrpp_lookahead/config.py) avalia a mesma expressão para uma rota
inteira, e é o único sítio onde a jornada de trabalho está definida — o solver,
os KPI por rota, a avaliação de rotas externas fixadas e os relatórios chamam-no
todos, pelo que nenhuma parte do projeto pode discordar de outra sobre o que é
uma hora de trabalho.

---

## 5. Classificação lookahead

Executada antes da otimização, sobre o estado no início do dia. Seja $\ell_i$ o
nível atual do contentor $i$ em kg (no dia 1, $\ell_i = S_i$).

**MustGo** — atinge o limiar amanhã:

$$
i \in \mathcal{MG}
\iff
\ell_i + \alpha_i \;\ge\; \frac{\theta^{MG}}{100}\,\mathrm{CAP}_i
$$

**MustGo-LookAhead** — não é MustGo, mas transborda algures dentro do horizonte:

$$
i \in \mathcal{LA}
\iff
i \notin \mathcal{MG}
\;\wedge\;
\exists\, k \in \{2, \dots, W\} :\;
\ell_i + k\,\alpha_i \;\ge\; \frac{\theta^{OVF}}{100}\,\mathrm{CAP}_i
$$

Tudo o resto é **Opcional**: o solver só o recolhe se compensar.

Com os valores por omissão $\theta^{MG} = \theta^{OVF} = 100\,\%$ as duas regras
leem-se naturalmente: *MustGo* transborda amanhã, *MustGoLA* transborda dentro
de $W$ dias. Baixar $\theta^{MG}$ torna a política mais conservadora, forçando a
recolha antes de o contentor estar fisicamente cheio.

A justificação económica de $\mathcal{LA}$ é que um contentor próximo de uma
rota que hoje já vai ser percorrida sai muito mais barato de esvaziar agora do
que de alcançar com uma deslocação dedicada daqui a três dias. A classificação
promove-o a obrigatório para que o otimizador não o possa adiar para um futuro
mais caro.

> **Ambos os conjuntos são forçados.** No código o conjunto obrigatório é
> $\mathcal{F} = \mathcal{MG} \cup \mathcal{LA}$ — os contentores MustGoLA são
> restringidos com a mesma dureza que os MustGo. A distinção sobrevive apenas
> nos relatórios, através da classificação *original*. É por isto que os KPI por
> rota do próprio solver mostram todos os contentores forçados como "MustGo":
> ver a [secção 14](#14-mapa-equação-código).

> **O turno interage com esta classificação.** A restrição 8.8 força cada
> contentor de $\mathcal{F}$ e a restrição 8.13 limita a jornada. Se o conjunto
> forçado não puder ser servido dentro de $K^{\max}$ turnos, as duas são
> conjuntamente inviáveis — um modo de falha que a formulação 1 não podia ter, e
> que o ajuste de capacidade de frota da secção 9.2 **não** deteta, por
> raciocinar apenas em quilogramas. Ver a
> [secção 10.3](#103-uma-armadilha-de-inviabilidade-que-convém-conhecer).

---

## 6. Variáveis de decisão

| Variável | Domínio | Significado |
|---|---|---|
| $x_{ij}$ | $\{0,1\}$, $(i,j) \in A$ | o arco $(i,j)$ é percorrido |
| $g_i$ | $\{0,1\}$, $i \in N_R$ | o contentor $i$ é recolhido |
| $y_{ij}$ | $\mathbb{R}_{\ge 0}$, $(i,j) \in A$ | carga transportada no arco $(i,j)$ [kg] |
| $f_{ij}$ | $\mathbb{R}_{\ge 0}$, $(i,j) \in A$ | fluxo unitário no arco $(i,j)$ — apenas conetividade |
| $t_{ij}$ | $\mathbb{R}_{\ge 0}$, $(i,j) \in A$ | **minutos decorridos à chegada a $j$ pelo arco $(i,j)$** |
| $k$ | $\mathbb{Z}$, $0 \le k \le K^{\max}$ | número de rotas utilizadas |

A variável $t_{ij}$ é nova nesta formulação e só é criada **quando
`shift.enforce` é verdadeiro** — com o interruptor desligado o modelo é
literalmente a formulação 1, não uma relaxação da formulação 2.

Passam a existir três variáveis de fluxo sobre o mesmo conjunto de arcos, e
mantêm-se deliberadamente separadas:

* $y_{ij}$ é **física**: quilogramas efetivamente dentro do camião nesse arco. É
  ela que impõe a capacidade $Q$.
* $f_{ij}$ é **fictícia**: uma unidade de uma mercadoria abstrata enviada do
  depósito a cada contentor servido. Não tem significado físico e existe apenas
  para proibir subrotas. Fundi-la com $y$ seria possível, mas enfraqueceria a
  relaxação linear, porque um contentor com $S_i = 0$ não transportaria carga
  física e poderia então situar-se num ciclo desligado.
* $t_{ij}$ é **temporal**: minutos decorridos desde a saída do depósito, medidos
  no momento da chegada a $j$. É ela que impõe o turno.

As três partilham uma estrutura — um fluxo mono-produto acumulado ao longo da
rota —, razão pela qual o mesmo artifício contabilístico funciona três vezes. O
que difere é aquilo que cada uma acumula: $y$ ganha $S_i$ em cada nó, $f$ ganha
uma unidade, e $t$ ganha $s$ em cada nó mais $\tau_{ij}$ em cada arco.

---

## 7. Função objetivo

$$
\max \;\;
\underbrace{R \sum_{i \in N_R} S_i\, g_i}_{\text{receita}}
\;-\;
\underbrace{C \sum_{(i,j) \in A} D_{ij}\, x_{ij}}_{\text{custo de deslocação}}
\;-\;
\underbrace{\Omega \, k}_{\text{custo de frota}}
$$

Maximiza-se o **lucro líquido em euros**, não se minimiza a distância. Três
consequências que vale a pena enunciar:

* Um contentor só é recolhido quando a sua receita marginal $R\,S_i$ excede o
  custo marginal do desvio $C \cdot \Delta D$ — a menos que seja forçado.
* Como $R$ multiplica $S_i$, um contentor cheio vale mais do que um vazio, pelo
  que o modelo prefere naturalmente pontos densos e cheios.
* O termo $\Omega k$ leva o modelo a *preferir menos veículos*, tudo o resto
  igual. Com $\Omega = 0,1$ € isto é um critério de desempate e não um
  verdadeiro motor de decisão; aumentá-lo substancialmente altera a decisão
  sobre a dimensão da frota.

> **A função objetivo mantém-se igual à da formulação 1: o tempo continua sem
> preço.** É intencional e não é um lapso. O tempo da equipa entra como
> *orçamento* (restrição 8.13), não como custo. O modelo pode portanto usar o
> turno inteiro sempre que isso compense, e é o que faz: na instância de
> referência as duas rotas saem com 8,00 h de um limite de 8 h.
>
> Se o operador pagar por hora trabalhada, a alteração honesta é acrescentar à
> função objetivo um termo $-\,c^{\text{equipa}} \sum_{(i,j) \in A} \tau_{ij}
> x_{ij} - c^{\text{equipa}} s \sum_{i \in N_R} g_i$. É um modelo diferente com
> um ótimo diferente: trocaria horas por quilogramas em vez de gastar todas as
> horas disponíveis. Qual dos dois está certo depende de a equipa ser paga ao
> turno ou à hora — um facto sobre o contrato, não sobre a matemática.

---

## 8. Restrições

As restrições 8.1 a 8.9 transitam da formulação 1. As restrições 8.10 a 8.13 são
novas e só são acrescentadas **se `shift.enforce` for verdadeiro**.

### 8.1 Dimensão da frota

$$
k \;\le\; K^{\max}
$$

Imposta também como limite superior da variável. Com um $K^{\max}$ pequeno, a
capacidade de frota $K^{\max} \cdot Q$ pode não conseguir albergar todos os
contentores forçados, o que desencadeia o ajuste da
[secção 9.2](#92-ajuste-dos-mustgo-à-frota-disponível).

### 8.2 Restrições de grau — visitar equivale a recolher

$$
\sum_{i \,:\, (i,j) \in A} x_{ij} \;=\; g_j
\qquad \forall j \in N_R
$$

$$
\sum_{t \,:\, (j,t) \in A} x_{jt} \;=\; g_j
\qquad \forall j \in N_R
$$

O grau de entrada e o grau de saída de cada contentor igualam a sua variável de
seleção. Um contentor servido é entrado exatamente uma vez e deixado exatamente
uma vez; um contentor não servido fica isolado.

Isto acopla $x$ e $g$ de forma apertada, e significa que **um veículo não pode
atravessar um contentor sem o esvaziar** — não existe estado de "passagem" neste
modelo. A consequência torna-se mais aguda com o tempo restringido: um contentor
situado no caminho mais barato entre dois contentores servidos não pode ser
contornado de graça, pelo que a geometria de uma rota e o conjunto de
contentores que ela serve são a mesma decisão.

### 8.3 Saídas e chegadas ao depósito

$$
k \;=\; \sum_{j \,:\, (0,j) \in A} x_{0j}
\qquad\qquad
\sum_{j \,:\, (j,0) \in A} x_{j0} \;=\; k
$$

O número de rotas é definido como o número de arcos que saem do depósito, e
outros tantos têm de regressar. Em conjunto com as restrições de grau, isto faz
de cada componente conexa que contenha contentores servidos um percurso fechado
pelo depósito.

### 8.4 Capacidade e ativação de arco

$$
y_{ij} \;\le\; Q\, x_{ij}
\qquad \forall (i,j) \in A
$$

$$
f_{ij} \;\le\; \lvert N_R \rvert \, x_{ij}
\qquad \forall (i,j) \in A
$$

Ambos os fluxos só podem circular em arcos percorridos, e a carga física nunca
excede a capacidade do veículo. O big-$M$ da segunda restrição é
$\lvert N_R \rvert$, o maior número de contentores que uma única rota poderia
servir.

### 8.5 Propagação da carga

$$
\sum_{j \,:\, (i,j) \in A} y_{ij}
\;-\;
\sum_{j \,:\, (j,i) \in A} y_{ji}
\;=\;
S_i\, g_i
\qquad \forall i \in N_R
$$

A carga que sai do contentor $i$ menos a que nele entra iguala o que aí foi
recolhido. Encadeada ao longo de uma rota, acumula o peso recolhido e, combinada
com $y_{ij} \le Q x_{ij}$, limita cada rota a $Q$ quilogramas sem qualquer
restrição de capacidade explícita por rota.

### 8.6 Os veículos saem vazios do depósito

$$
y_{0j} \;=\; 0
\qquad \forall j \,:\, (0,j) \in A
$$

Sem isto, a carga poderia ser inicializada arbitrariamente e a restrição de
capacidade ficaria vazia de conteúdo.

### 8.7 Limite inferior da carga (desigualdade válida)

$$
y_{ij} \;\ge\; S_i \, x_{ij}
\qquad \forall (i,j) \in A,\; i \neq 0
$$

Não é necessária para a correção — é implicada por 8.5 e 8.6 em soluções
inteiras — mas **reforça consideravelmente a relaxação linear**. Se o arco
$(i,j)$ for usado, então $g_i = 1$ por 8.2, logo o camião sai de $i$
transportando pelo menos o $S_i$ que acabou de recolher. Enunciá-la
explicitamente impede a relaxação linear de repartir carga fracionariamente
pelos arcos.

### 8.8 Contentores obrigatórios

$$
g_i \;=\; 1
\qquad \forall i \in \mathcal{F}
$$

Onde $\mathcal{F}$ é o conjunto forçado **depois** do ajuste da secção 9.2. Os
contentores despromovidos por esse ajuste perdem esta restrição e revertem para
opcionais — o solver pode continuar a recolhê-los se compensar.

### 8.9 Eliminação de subrotas — fluxo mono-produto

$$
\sum_{j \,:\, (0,j) \in A} f_{0j}
\;=\;
\sum_{j \in N_R} g_j
$$

$$
\sum_{i \,:\, (i,j) \in A} f_{ij}
\;-\;
\sum_{t \,:\, (j,t) \in A} f_{jt}
\;=\;
g_j
\qquad \forall j \in N_R
$$

O depósito injeta exatamente uma unidade de fluxo por contentor servido, e cada
contentor servido absorve exatamente uma unidade. Qualquer ciclo desligado do
depósito teria de absorver fluxo que nunca recebe, logo é inviável.

Esta formulação, no estilo de **Gavish–Graves**, é preferida à de
Miller–Tucker–Zemlin por dar uma relaxação mais apertada, e aos cortes
exponenciais de eliminação de subrotas por manter o modelo numa única resolução,
sem callbacks. Custa $\lvert A \rvert$ variáveis contínuas adicionais.

---

### 8.10 Turno — ativação de arco

$$
t_{ij} \;\le\; H^{\min}\, x_{ij}
\qquad \forall (i,j) \in A
$$

O tempo só pode decorrer em arcos efetivamente percorridos, e nenhuma chegada
pode ser posterior ao fim do turno. É o acoplamento big-$M$ de $t$, exatamente
paralelo a $y_{ij} \le Q x_{ij}$, com $H^{\min}$ no papel de $Q$.

### 8.11 Turno — partida do depósito

$$
t_{0j} \;=\; \tau_{0j}\, x_{0j}
\qquad \forall j \,:\, (0,j) \in A
$$

Cada equipa inicia o turno no depósito com o relógio a zero, pelo que o tempo
decorrido à chegada ao primeiro contentor é exatamente o tempo de percurso desse
primeiro arco. É a contraparte temporal de 8.6 — sem ela, o relógio poderia ser
inicializado em qualquer valor e o limite de turno ficaria vazio de conteúdo.

Repare-se que é uma **igualdade** e não um limite superior: fixar o início torna
o tempo acumulado ao longo da rota exato, e não apenas limitado, o que é o que
permite enunciar a restrição 8.13 sobre um único arco.

### 8.12 Turno — propagação do tempo

$$
\sum_{j \,:\, (i,j) \in A} t_{ij}
\;-\;
\sum_{j \,:\, (j,i) \in A} t_{ji}
\;=\;
s\, g_i
\;+\;
\sum_{j \,:\, (i,j) \in A} \tau_{ij}\, x_{ij}
\qquad \forall i \in N_R
$$

O núcleo do modelo da jornada de trabalho, e o análogo exato da propagação da
carga 8.5. Leia-se num contentor servido $i$: a equipa chega com $t_{ji}$
minutos no relógio, gasta $s$ minutos a esvaziar os contentores, e conduz
$\tau_{ij}$ minutos até ao contentor seguinte, chegando lá com
$t_{ij} = t_{ji} + s + \tau_{ij}$.

Como 8.2 obriga a exatamente um arco de saída quando $g_i = 1$, o somatório
$\sum_j \tau_{ij} x_{ij}$ reduz-se ao tempo de percurso do único arco
efetivamente tomado. Quando $g_i = 0$ o contentor está isolado, todos os $x$ e
$t$ incidentes são nulos, e a restrição lê-se $0 = 0$.

Se o tempo de serviço dever escalar com o número de contentores do ponto,
substitui-se $s\,g_i$ por $s\,\mathrm{Ncont}_i\,g_i$. Nada mais na formulação se
altera.

### 8.13 Turno — a jornada de trabalho é limitada

$$
t_{j0} \;\le\; H^{\min}\, x_{j0}
\qquad \forall j \,:\, (j,0) \in A
$$

É um caso particular de 8.10, enunciado à parte por ser a restrição que faz o
trabalho. Por 8.11 e 8.12, a grandeza $t_{j0}$ é o tempo total decorrido da rota
quando a equipa regressa ao depósito — saída, todas as paragens de serviço e a
viagem de regresso incluídas. Limitá-la limita portanto a jornada inteira.

Duas propriedades desta construção merecem ser enunciadas:

* **A viagem de regresso é contabilizada.** Uma formulação que limitasse o tempo
  no último contentor deixaria uma rota terminar longe do depósito sem custo.
  Aqui o arco $(j,0)$ transporta o seu próprio $\tau_{j0}$ através de 8.12, pelo
  que voltar a casa é pago.
* **O limite é por rota, não agregado.** Cada rota chega ao depósito pelo seu
  próprio arco, e cada um desses arcos é limitado separadamente. Duas rotas de
  8 h são viáveis; uma rota de 16 h não é. É esta a leitura correta de um turno
  — limita uma equipa, e duas equipas a trabalhar em paralelo não se somam.

> **Porquê uma formulação de fluxo e não sequenciação com big-$M$.** A
> alternativa clássica é $u_j \ge u_i + s + \tau_{ij} - M(1 - x_{ij})$ sobre
> variáveis de nó $u$. Exige uma restrição big-$M$ por arco com $M \approx
> H^{\min}$, e a sua relaxação linear é fraca: um $x$ fracionário desativa a
> restrição quase por completo. A forma em fluxo acima reutiliza a estrutura já
> comprovada em $y$ e $f$, mantém cada coeficiente na sua escala natural, e
> acrescenta $\lvert A \rvert$ variáveis contínuas em vez de $n$ — uma troca de
> memória por aperto que compensa aqui, ainda que não seja gratuita: ver a
> [secção 13](#13-dimensão-do-modelo-e-método-de-resolução).

---

## 9. Pré-processamento

Correm três transformações antes da resolução. As duas primeiras alteram a
região admissível; a terceira apenas ajuda a pesquisa.

### 9.1 Filtragem de arcos

O dígrafo completo sobre 492 nós tem 241 572 arcos, o que é impraticável. O
conjunto de arcos $A$ retém:

1. **Todos os arcos incidentes no depósito**, $(0,j)$ e $(j,0)$ —
   incondicionalmente, já que removê-los poderia tornar o problema inviável.
2. **Arcos de $\kappa$ vizinhos mais próximos, bidirecionais**: $(i,j)$
   sobrevive se $j$ estiver entre os $\kappa$ nós mais próximos de $i$ *ou* se
   $i$ estiver entre os $\kappa$ mais próximos de $j$, com $\kappa =$
   `solver.knn`. A disjunção é importante — uma regra unilateral quebraria a
   simetria de forma a descartar bons arcos em zonas esparsas.
3. Opcionalmente, com `solver.keep_mustgo_arcs = true`, todos os arcos entre
   dois contentores forçados. Desativado por omissão, para reproduzir
   exatamente os notebooks originais.

E depois **remove** qualquer arco sobrevivente com

$$
S_i + S_j \;>\; Q
$$

visto que dois contentores cuja soma de conteúdos excede a capacidade do veículo
nunca podem ser consecutivos numa rota admissível.

> Esta última regra só morde quando $Q$ é pequeno face aos níveis dos
> contentores. Na instância de referência, com $Q = 4000$ kg e um conteúdo
> máximo de 120 kg por contentor, remove **zero** arcos; aí a redução vem
> inteiramente da regra dos $\kappa$ vizinhos mais próximos.

> **Isto é uma restrição heurística, não uma redução válida.** Filtrar por
> $\kappa$ vizinhos mais próximos pode, em princípio, excluir o verdadeiro
> ótimo. O gap MIP reportado é, portanto, um gap *relativo ao problema
> filtrado*, e a afirmação honesta é que a solução é ótima para o conjunto de
> arcos restrito. Aumentar `knn` alarga $A$ e atenua essa ressalva, ao custo de
> tempo de resolução.

### 9.2 Ajuste dos MustGo à frota disponível

Se os contentores forçados pesarem mais do que a frota consegue transportar,

$$
\sum_{i \in \mathcal{F}} S_i \;>\; K^{\max} \cdot Q ,
$$

o modelo tal como enunciado é **inviável**: as restrições 8.1, 8.5 e 8.8 não
podem valer simultaneamente. Em vez de falhar, o código retém gulosamente os
contentores mais cheios.

Os contentores são ordenados pelo **rácio de enchimento** $S_i / \mathrm{CAP}_i$
por ordem decrescente e aceites enquanto o total acumulado couber em
$K^{\max} Q$; os restantes são despromovidos a opcionais e registados com o
motivo `MG_downgraded_fleet_full`.

Ordenar por *rácio* e não por peso absoluto é deliberado: um contentor de 40 kg
a 95 % da capacidade está mais perto de transbordar do que um de 120 kg a 50 %,
e o transbordo é aquilo que a política procura evitar.

Duas propriedades a ter presentes:

* Trata-se de uma **mochila gulosa**, não exata. Não maximiza o peso arrumado em
  $K^{\max} Q$.
* Um contentor despromovido não é excluído — apenas perde a sua restrição de
  forçagem, e o otimizador pode ainda servi-lo se compensar.

> **Esta verificação é apenas sobre quilogramas.** Não testa se os contentores
> forçados são *alcançáveis* dentro de $K^{\max}$ turnos. Ver a
> [secção 10.3](#103-uma-armadilha-de-inviabilidade-que-convém-conhecer).

### 9.3 Solução inicial (warm start)

Uma heurística de vizinho mais próximo constrói até $K^{\max}$ rotas e entrega o
$(x, g, k)$ resultante ao Gurobi como solução inicial (MIP start). Apenas
acelera a pesquisa; não restringe a solução, e o Gurobi descarta-a se violar
alguma restrição.

São-lhe exigidas aqui duas propriedades, e a solução inicial da formulação 1 não
tinha nenhuma delas:

* **Tem de alcançar todos os contentores forçados.** Caminhar gulosamente de um
  MustGo para o seguinte não funciona sob filtragem de arcos: $\kappa$ retém os
  vizinhos mais próximos de cada nó, e esses na sua maioria *não* são forçados,
  pelo que o percurso fica sem arcos admissíveis ao fim de meia dúzia deles. A
  heurística admite portanto contentores opcionais como **pontos de passagem**,
  escolhidos de forma a minimizar $D_{\text{atual},j} + D_{j,\text{alvo}}$, onde
  *alvo* é o contentor forçado pendente mais próximo — proximidade ao objetivo,
  e não barateza do salto, que é o que impede o percurso de se desviar.
* **Tem de respeitar o turno quando `enforce` está ativo.** Uma solução inicial
  que viole a restrição acabada de acrescentar é rejeitada, e num modelo desta
  dimensão o solver pode então não encontrar incumbente nenhum dentro do limite
  de tempo. O percurso só se estende a $j$ se ainda conseguir servir $j$ e
  regressar: $t_{\text{atual}} + \tau_{\text{atual},j} + s + \tau_{j,0} \le
  H^{\min}$.

Na instância de referência, a primeira propriedade eleva a cobertura de
contentores forçados de 25/100 para 100/100. Sem ela, uma corrida de 600 s com o
turno ativo terminou sem encontrar qualquer solução admissível; com ela, o
Gurobi carrega uma solução inicial no valor de 442,02 € em menos de um décimo de
segundo.

> **Nota de reprodutibilidade.** Alterar a solução inicial não altera a região
> admissível, mas altera o caminho de pesquisa. As corridas publicadas antes
> desta alteração não são, portanto, reproduzíveis bit a bit com o código atual,
> mesmo com a semente e o número de threads fixados. Comparações que precisem de
> atribuir uma diferença a um só parâmetro devem correr novamente ambos os lados
> com o mesmo código.

---

## 10. O que a jornada de trabalho altera

Tudo o que consta desta secção foi medido na instância de referência — 491
contentores, aglomerado C7, $Q = 4000$ kg, $K^{\max} = 2$, $v = 30$ km/h,
$s = 1,5$ min, $H = 8$ h — resolvida durante 6 h com um gap alvo de 5 %.

### 10.1 O ótimo desloca-se

| | `enforce: false` | `enforce: true`, $H = 8$ h |
|---|---|---|
| Contentores recolhidos | 317 | 257 |
| Peso | 7 999,3 kg | 7 539,1 kg |
| Distância | 290,37 km | 287,16 km |
| Lucro | 1 009,32 € | 937,74 € |
| Rota mais longa | **9,58 h** | **8,00 h** |
| Capacidade usada | 100,0 % / 99,98 % | 99,7 % / 88,8 % |
| Gap provado | 2,35 % | 11,16 % |

A restrição custa cerca de 7 % do lucro e 60 contentores. Não é uma alteração de
relatório: a solução anterior é inviável sob ela, pelo que se trata de um ótimo
genuinamente diferente e não das mesmas rotas remedidas.

### 10.2 A restrição ativa muda de identidade

Este é o resultado estrutural, e é mais interessante do que os 7 %.

Sem o turno, ambas as rotas enchem até à capacidade — o veículo é o
estrangulamento. Com o turno, a rota 1 enche a 99,7 % mas **a rota 2 para a
88,8 %**, deixando 450 kg de capacidade que não consegue usar porque o relógio
acabou primeiro. Ambas as rotas terminam com 479,9 minutos de um limite de 480.

O estrangulamento passa portanto de **quilogramas para minutos**. Qualquer
conclusão do tipo "a frota é pequena de mais" tem de ser reexaminada assim que a
jornada de trabalho é modelada: nesta instância a frota não é pequena, o dia é
que é curto.

Uma segunda consequência, não planeada: a solução com turno é *equilibrada*
(8,00 h e 8,00 h), ao passo que a solução sem turno não era (9,58 h e 8,03 h). A
restrição entrega gratuitamente uma propriedade de equilíbrio de carga de
trabalho que nunca foi pedida à função objetivo — ver a linha correspondente na
[secção 12](#12-o-que-o-modelo-continua-a-não-captar).

### 10.3 Uma armadilha de inviabilidade que convém conhecer

A restrição 8.8 força cada contentor de $\mathcal{F}$; a restrição 8.13 limita
cada rota a $H^{\min}$. Em conjunto podem ser inviáveis mesmo com capacidade de
frota abundante, porque a verificação da secção 9.2 raciocina em **peso** e o
recurso escasso pode ser o **tempo**.

Uma condição suficiente aproximada para o conjunto forçado ser servível é

$$
\underbrace{s\,\lvert \mathcal{F} \rvert}_{\text{serviço}}
\;+\;
\underbrace{\text{(condução necessária para visitar } \mathcal{F})}_{\text{depende da rota}}
\;\le\; K^{\max} H^{\min},
$$

cujo segundo termo é ele próprio um problema de encaminhamento e, portanto, não
está disponível antes da resolução. Na instância de referência a folga é
confortável — 100 contentores forçados precisam de 150 min de serviço contra um
orçamento de 960 min, restando 810 min para condução onde cerca de 200 chegam —
pelo que a armadilha não dispara. Com uma política mais densa (um $\theta^{MG}$
mais baixo, uma janela $W$ mais longa) ou um turno mais curto, pode disparar, e
o sintoma é um modelo inviável em vez de um aviso.

**O código não faz atualmente esta verificação.** A secção 9.2 reportará que o
conjunto forçado cabe em $K^{\max} Q$ quilogramas e prosseguirá. Estender essa
verificação ao tempo exigiria um limite para o esforço de encaminhamento sobre
$\mathcal{F}$ — a solução inicial da secção 9.3 já calcula um percurso
admissível sobre o conjunto forçado e seria o sítio natural para o derivar.

---

## 11. Simulação multi-dia

Ao longo de `lookahead.days` dias consecutivos, o estado é transportado para a
frente. Fixadas as rotas do dia, e sendo $\mathcal{S}$ o conjunto de contentores
efetivamente servidos:

$$
\ell_i^{\,t+1} =
\begin{cases}
0 & i \in \mathcal{S} \\[4pt]
\min\!\big(\mathrm{CAP}_i,\; \ell_i^{\,t} + \alpha_i\big) & i \notin \mathcal{S}
\end{cases}
$$

A recolha esvazia completamente um contentor; todos os outros acumulam um dia de
resíduos, **truncado na capacidade**. É esse truncamento que torna o transbordo
uma perda: os resíduos que chegam a um contentor cheio desaparecem do modelo em
vez de se acumularem, pelo que um contentor negligenciado deixa de contribuir
para a receita. É precisamente essa assimetria que dá valor à política de
lookahead.

Cada dia é resolvido como um MILP independente. **Não há** otimização entre
dias: o modelo é míope para além do horizonte de classificação, e os conjuntos
do lookahead são o único mecanismo que transporta informação futura para a
decisão de hoje.

> **Com `days: 1` o mecanismo de lookahead não é exercitado.** Um único dia
> força os contentores de $\mathcal{LA}$ mas nunca chega ao dia em que
> recolhê-los antecipadamente teria compensado. Na instância de referência os 31
> contentores MustGoLA têm um enchimento mediano de 80 %, ao passo que o solver
> escolheu livremente contentores opcionais até aos 2 %, pelo que os 31 seriam
> recolhidos de qualquer forma e a classificação não alterou nada. Demonstrar o
> valor do lookahead exige um `days` bastante superior à janela $W$, comparando
> $W = 2$ contra $W = 1$.

---

## 12. O que o modelo continua a não captar

Enunciado explicitamente, porque cada um destes pontos é uma preocupação
operacional real sobre a qual a formulação é omissa. As duas primeiras linhas da
tabela da formulação 1 passaram a estar modeladas e foram removidas.

| Não modelado | Consequência |
|---|---|
| **Janelas temporais** | Os contentores podem ser servidos a qualquer momento dentro do turno; sem restrições de acesso ou de ruído. |
| **Tempo da equipa como custo** | O tempo é orçamento, não preço. O modelo gasta o turno inteiro sempre que compensa — ver a nota da [secção 7](#7-função-objetivo). |
| **Equilíbrio de carga entre motoristas** | Não é restrição. Verificou-se com $H = 8$ h porque o turno limitou ambas as rotas; não está garantido. |
| **Pausas e descanso legal** | $H$ é um bloco contínuo único. Uma pausa obrigatória dentro do turno reduziria o orçamento produtivo abaixo de $H$. |
| **Tempo de serviço variável** | $s$ é constante; não escala com $\mathrm{Ncont}_i$ nem com o grau de enchimento dos contentores. |
| **Velocidade variável por zona ou hora** | $v$ é uma média única para toda a volta; troços urbanos e rurais são tratados de igual modo. |
| **Frota heterogénea** | Todos os veículos partilham uma capacidade $Q$, um custo $\Omega$ e um turno $H$. |
| **Múltiplos depósitos ou descarga intermédia** | Um só depósito; um veículo nunca descarrega a meio da rota, pelo que $Q$ limita o dia inteiro. |
| **Taxas de enchimento estocásticas** | $\alpha_i$ é uma média diária determinística; a variância entre dias é ignorada. |
| **Variabilidade do tráfego** | $D_{ij}$ é um instantâneo estático do perfil ORS; $v$ não varia com o congestionamento. |
| **Penalização do transbordo** | Os resíduos que chegam a um contentor cheio desaparecem (secção 11); falhar um MustGo não custa nada além da receita perdida. |

---

## 13. Dimensão do modelo e método de resolução

Para a instância de referência — 491 contentores, 492 nós, $\kappa = 25$:

| Grandeza | `enforce: false` | `enforce: true` |
|---|---|---|
| Nós | 492 | 492 |
| Arcos no dígrafo completo | 241 572 | 241 572 |
| Arcos após filtragem | 16 866 — **7,0 %** retidos | 16 866 |
| Variáveis binárias $x$ | 16 866 | 16 866 |
| Variáveis binárias $g$ | 491 | 491 |
| Variáveis contínuas | 33 732 ($y, f$) | **50 598** ($y, f, t$) |
| Restrições | $O(\lvert A \rvert)$ | $O(\lvert A \rvert)$, ~1,5× mais |

A filtragem é o que torna a instância resolúvel de todo: descarta 93 % dos arcos
antes de o Gurobi ver o modelo.

**A restrição de turno é cara.** Acrescenta um terceiro fluxo sobre o mesmo
conjunto de arcos, fazendo o modelo pré-processado crescer para cerca de 67 000
linhas e colunas. Medido na instância de referência, o mesmo orçamento de 6 h que
provou um gap de 2,35 % sem ela provou apenas 11,16 % com ela. O custo não está
em encontrar boas soluções — o incumbente subiu consistentemente de 442 € para
938 € — mas no **limite dual**, que estagnou em 1 042,44 € ao fim de 80 minutos e
não voltou a mexer nas quatro horas seguintes.

Essa estagnação é diagnosticável e não misteriosa. O log mostra ~21 000 iterações
de simplex por nó de branch-and-bound e apenas 1 483 nós explorados em seis
horas: com `node_method: 2` (barrier), cada relaxação linear de nó é resolvida de
raiz, já que o barrier não reaproveita a base do nó pai. Para corridas com o
turno ativo, `node_method: 1` (simplex dual), `mip_focus: 3` (limite em vez de
admissibilidade) e um `heuristics` mais baixo são os parâmetros a experimentar
primeiro.

Resolvido com o Gurobi sob `MIP_GAP` e `TIME_LIMIT`. O solver para no que
ocorrer primeiro e reporta sempre o gap que conseguiu provar, pelo que uma
corrida limitada no tempo produz na mesma uma solução utilizável com um limite
de qualidade quantificado. Uma solução parada num gap não nulo é admissível e o
seu valor objetivo é um **limite inferior** válido do ótimo — que é a única
afirmação que sobre ela deve ser feita.

A licença Gurobi gratuita, limitada em dimensão, não suporta um modelo deste
tamanho; é necessária uma licença académica ou comercial.

---

## 14. Mapa equação-código

| Formulação | Código |
|---|---|
| $\mathrm{CAP}_i$, $S_i$, $\alpha_i$ (4.1) | [instance.py](src/vrpp_lookahead/instance.py) — `load_instance` |
| $\tau_{ij}$, $H^{\min}$, duração de rota (4.2) | [config.py](src/vrpp_lookahead/config.py) — `Shift.travel_min`, `Shift.route_min`, `Shift.max_shift_min` |
| $\mathcal{MG}$, $\mathcal{LA}$ (secção 5) | [lookahead.py](src/vrpp_lookahead/lookahead.py) — `lookahead` |
| Filtragem de arcos (9.1) | [vrpp.py](src/vrpp_lookahead/vrpp.py) — `_arcs` |
| Ajuste dos MustGo (9.2) | [vrpp.py](src/vrpp_lookahead/vrpp.py) — `_adjust_mustgo` |
| Solução inicial (9.3) | [vrpp.py](src/vrpp_lookahead/vrpp.py) — `_warm_start` |
| Variáveis $x, y, f, g, k$ (secção 6) | `solve_vrpp` — `mdl.addVars(...)` |
| Variável $t$ (secção 6) | `solve_vrpp` — dentro de `if sh.enforce:` |
| Função objetivo (secção 7) | `solve_vrpp` — `mdl.setObjective(...)` |
| Restrições 8.1 – 8.9 | `solve_vrpp` — o bloco principal de `mdl.addConstr(...)` |
| Restrições 8.10 – 8.13 | `solve_vrpp` — o bloco `if sh.enforce:` |
| Reconstrução das rotas | [vrpp.py](src/vrpp_lookahead/vrpp.py) — `_extract_routes` |
| Jornada por rota, relatórios | [reporting.py](src/vrpp_lookahead/reporting.py) — `kpi_per_route` |
| Jornada de rotas externas fixadas | [fixed_routes.py](src/vrpp_lookahead/fixed_routes.py) — `evaluate_fixed_routes` |
| Comparação normalizada por hora-equipa | [scripts/06_shift_analysis.py](scripts/06_shift_analysis.py) |
| Atualização de estado (secção 11) | [simulation.py](src/vrpp_lookahead/simulation.py) — `simulate` |

### Nota sobre a classificação reportada

Em `solve_vrpp`, o indicador de forçagem `crit` é inicializado a partir de
$\mathcal{MG} \cup \mathcal{LA}$, e a etiqueta por nó `kind` é depois derivada
como *"MustGo se forçado, senão MustGoLA se originalmente MustGoLA, senão
Optional"*. Uma vez que os contentores MustGoLA também são forçados, acabam
etiquetados como `MustGo` na folha de KPI por rota `3_KPI_Routes` — razão pela
qual essa folha pode reportar `N_MustGoLA = 0` enquanto o lookahead identificou
31.

A repartição verdadeira é preservada em `kind_orig` e é a que as folhas
`5_MustGo` e `6_MustGoLA` usam. Qualquer análise que precise da separação
genuína MustGo / MustGoLA deve ler essas folhas, ou a classificação em
`1_Lookahead` — nunca as contagens por rota.

### Reproduzir as duas formulações

```bash
# Formulação 1 — sem jornada de trabalho (shift.enforce: false)
python scripts/03_run_vrpp.py --config config/instance_491_C7_gap1.yaml

# Formulação 2 — jornada de 8 h imposta
python scripts/03_run_vrpp.py --config config/instance_491_C7_shift8h.yaml

# Comparar as duas em igualdade de horas-equipa
python scripts/06_shift_analysis.py --config config/instance_491_C7_shift8h.yaml \
    --external "EVOX routes fixed in our model=data/raw/evox_routes_491_C7.yaml" \
    --solution "VRPP no shift=results/491_C7_gap1/Day_01/result_Day_01.xlsx" \
    --solution "VRPP 8 h shift=results/491_C7_shift8h/Day_01/result_Day_01.xlsx"
```
