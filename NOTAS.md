# Projeto: mostrador de ponteiro (painel auto) para CPU/RAM

## Objetivo
Reaproveitar um motor de ponteiro de painel de instrumentos automóvel
para mostrar CPU ou RAM do PC, comandado por Arduino via série.

## Hardware identificado
- **Motor:** VDO/Siemens **91 255 008** (versão de eixo longo; a de eixo
  curto é a 91 255 005; compatível com 91 255 112). Marcação no corpo:
  `91 255 008 04 00`. Recuperado de placa marcada `B 3682 / KW-16`.
- **Tipo:** motor de passo bipolar com caixa redutora. Confirmado por
  teste manual: tem detentes e batentes, não dá 360°, e **não volta a
  posição de repouso** quando largado (exclui air-core).
- **Pinos:** 4, com os pares **cruzados**.
- **Bobinas medidas:** 135 Ω e 142 Ω (escala de 200 Ω).
- **Sem datasheet público** — é peça de reparação, não componente de
  catálogo. Todos os parâmetros têm de ser obtidos empiricamente.
- **Arduino:** Uno na bancada; Nano clássico (ATmega328P) serve igual
  (clones podem precisar de "ATmega328P (Old Bootloader)"; pelo USB o 5V
  fica em ~4,6–4,7 V, um pouco menos de binário).
- **LCD 16×2** (HD44780), ligado em modo de 4 bits.

## Decisões de ligação
- 5 V / 135 Ω ≈ 37 mA por bobina. Limite absoluto do pino ATmega = 40 mA.
- **Ligação direta ao Arduino: só para testes de bancada.** Distribuir os
  4 pinos por portos diferentes (ex.: D2, D3, D8, D9) por causa do limite
  de 100 mA por porto. Não deixar energizado parado longos períodos.
- **Versão definitiva: driver de motor.** O L293D funciona, mas é bipolar
  e perde ~1–2 V nas saídas: a 5 V as bobinas ficavam com bem menos binário,
  e a descida já é o movimento mais apertado. **Preferir um módulo
  TB6612FNG** (MOSFET, queda desprezável, mesmos 4 pinos de comando). Em
  último caso, 4 × 100 Ω em série (≈21 mA, perde quase metade do binário).
- Díodos externos desnecessários a estas correntes.
- **Mudar a ligação do motor obriga a recalibrar `HOME_PHASE`.**

## Software (histórico)
- Previa-se a biblioteca **SwitecX25**; foi trocada por controlo próprio do
  motor em meio passo (ver resultados de bancada).
- **Homing** contra o batente no `setup()`. Patinar contra o batente é
  seguro eletricamente (corrente = V/R, não sobe com o rotor parado), mas
  é desgaste mecânico — limitar a poucos décimos de segundo.
- **Nunca rodar o eixo à mão** (danifica a redução interna, segundo os
  vendedores da peça). Nem para encostar o ponteiro ao zero.
- **Python com `psutil`**, envio por série de ambos os valores em cada
  ciclo (ex.: `C45 R72\n`); o Arduino escolhe qual mostrar, para o botão
  responder de imediato.
- `psutil.cpu_percent()` já vem normalizado 0–100 independentemente do
  número de núcleos. A escala 0–800% do `top` só aparece em medições
  **por processo**.
- **Filtro exponencial para CPU:** `s = s*0.96 + novo*0.04`, leituras a
  cada 50 ms (constante de tempo ≈ 1,2 s, igual à dos antigos 0,15 a 200 ms;
  passou a 50 ms para o ponteiro andar em passos mais pequenos). Custo medido:
  ~7 µs de CPU por leitura, ≈ 0,015% de um núcleo. **RAM dispensa filtro.**
- **Zona morta** de 2 passos antes de mandar mover, para eliminar tremor.
- Experimentado e revertido (2026-10-02): manter as bobinas ligadas e
  espalhar os passos pequenos pelo intervalo entre leituras. Ficou a versão
  com leituras a 50 ms.

## Design decidido
- **Um único ponteiro**, com **botão** a alternar entre CPU e RAM.
  Botão em `INPUT_PULLUP` a massa, debounce de 50 ms por software.
- **Varrimento 0→100→0** como self-test ao premir longo.
- **O LCD substitui as luzes de modo:** mostra a métrica que o ponteiro não
  mostra; ao trocar, mostra durante 3 s qual está no ponteiro.
- **Luzes de aviso** (casquilhos cor-de-rosa do painel) passam a avisos a
  sério, normalmente apagadas: CPU > 90% sustentado, fim de fase do
  Pomodoro, etc. LED a 5 V; as lâmpadas originais (~12 V / 1,2 W) exigiam
  fonte de 12 V. Cuidado com LED T5 "de carro": muitos são para 12 V.
- **Pomodoro (futuro):** no Arduino, para funcionar sem o PC. Segundo botão
  (curto = iniciar/pausar, longo = sair); trabalho 100%→0%, pausa sobe de
  volta a 100%; LCD com tempo exato e contagem; aviso por **piezo passivo**.
- **Folgas na escala:** 0% = 12 meios passos acima do batente
  (`POS_MIN = 12`), 100% = 12 abaixo do outro (`POS_MAX = 278`). Podem
  descer até 4–6 se a escala o pedir; menos que isso, o ponteiro bate.

## Resultados de bancada (2026-10-02, ligação direta D2/D3/D8/D9, sequência s0)
- O motor responde à sequência X25 (6 estados) da SwitecX25.
- Curso completo ≈ **220 passos** com s0 (6 estados) e **290** com s2
  (meio passo, 8 estados). Bate certo: 220 × 8/6 ≈ 293, ou seja ≈ 36,5
  ciclos elétricos de batente a batente. **290 confirmado de novo a
  2026-10-03.**
- Velocidade máxima sem rampa: **6 ms/passo**. Abaixo disso sobe bem,
  mas ao descer a partir do topo fica preso no arranque.
- Confirmado que não é dessincronização no batente: a 4 ms, entre 0 e 200
  (sem tocar nos batentes), sobe bem e encrava na descida. A causa é a
  falta de rampa, e o sentido anti-horário (descida) é o mais exigente.
- **Com s2 (meio passo) funciona a 1 ms/passo nos dois sentidos**, contra
  6 ms com s0. Varrimento completo em ≈ 0,3 s. Este motor deve ser um
  bipolar de 2 fases normal, e a sequência X25 (passos desiguais de 45° e
  90°) não lhe serve bem.
- Longe dos batentes (de 150 para 50 e volta), a 1 ms encrava na descida.
  **A 2 ms passou 10 ciclos sem perder passos.** O mínimo seguro com
  intervalo fixo é 2 ms.
- **Decisão:** usar s2 com 290 passos e controlo próprio do motor, em vez
  da SwitecX25. A rampa arranca a cerca de 5 ms e chega no máximo a 2 ms.
  Varrimento completo em ≈ 0,6 s. +300 contra o batente faz o ponteiro dar
  saltos: só no homing, devagar.
- **Antes de cada movimento, o rotor tem de assentar:** energizar a fase
  atual e esperar 20 ms (`SETTLE_MS`). Sem isto, o self-test e o homing não
  conseguiam descer a partir do topo. Com a espera, a descida passou a 3 ms
  por passo e o homing a 5 ms. Self-test, carga de 100% e estacionamento por
  timeout testados e a funcionar.
- **`HOME_PHASE = 6`** (2026-10-03, ligação direta, 3 medições iguais;
  `MAGIC` passou a `0xA9`). Com isto o homing acaba sempre no mesmo sítio,
  mesmo depois de puxar a ficha.

## Código
- `firmware/step_counter/` — sketch de bancada (comandos pela série).
- `firmware/gauge/` — sketch final. Versão anterior ao LCD em
  `firmware/gauge_backup/`.
- `pc/gauge.py` — script do PC (`pip install -r pc/requirements.txt`).
  Corre em macOS e Linux; arranca também no Windows (sem deteção do ecrã).
- Compilar sem o IDE: o `arduino-cli` vem dentro do Arduino IDE.app;
  passar `--libraries ~/Library/Arduino15/libraries` para achar o
  LiquidCrystal.

### Protocolo série (PC → Arduino)
| Linha | Significado |
|---|---|
| `C45.3 R72.1` | leituras de CPU e RAM (a cada 50 ms) |
| `T<linha><ponteiro><texto>` | texto do LCD: linha `0`/`1`, para quando o ponteiro mostra CPU (`C`) ou RAM (`R`) (a cada 0,25 s) |
| `G<0-7><16 hex>` | caractere personalizado 5×8, linha de cima primeiro; no texto usa-se `chr(n)`, exceto o slot 0, que é `chr(8)` (o byte 0 terminaria a linha). Não usar `8 + n` nos outros: 10 e 13 são `\n` e `\r` (a cada 5 s) |
| `B<0-255>` | brilho da iluminação (PWM no D10); `0` apaga o LCD (a cada 5 s) |
| `P` | estacionar; o Arduino responde `PARKED` |

Tudo o que não é leitura vai para uma fila e sai **uma linha por leitura**
(≤ ~32 bytes a cada 50 ms). O buffer de receção do Arduino tem só 64 bytes e
o firmware deixa de ler até 20 ms (`delay(SETTLE_MS)` ao arrancar o motor)
ou ~7–10 ms (redesenho do LCD); a 115200 baud chegam ~11,5 bytes/ms. Quando
se mandava tudo de uma vez (~94 bytes a cada 0,25 s), perdiam-se bytes e as
linhas colavam-se (texto baralhado, e uma linha `T` sem o início podia ser
lida como `C0 R0`).

`G`, `B` e o texto são reenviados mesmo sem mudanças: se o Arduino reiniciar
com a porta aberta (botão de reset, quebra de tensão), o PC não dá por isso e
o LCD perde os glifos. O firmware ignora glifos iguais, por isso o custo é
quase nulo. Alternativa não feita: reenviar só ao receber `READY`.

### Comportamento
- **Texto do LCD composto no `gauge.py`** (`lcd_rows()`, `GLYPHS`,
  `BRIGHTNESS`): para mudar o que aparece, só se mexe no Python. Hoje:
  linha 1 com o ícone da outra métrica (chip / pente de RAM; ou letra fixa
  C/R), a % e a app do topo (`▣ 5% Code`); linha 2 com ícone de ligar e o
  uptime em `HH:MMh` ou, a partir de 24 h, `DD:HHd` (largura fixa), e à
  direita a velocidade de download (`⏻ 03:26h   ↓1.2M`). O upload ficou de
  fora: não cabe nos 16 caracteres com o uptime neste formato.
  Nome da app comprido: desliza só o nome (ícone e % ficam parados), feito no
  Python, uma letra a cada 0,5 s e 2 s parado em cada ponta. O scroll do
  próprio LCD não serve: desloca as duas linhas juntas.
  O Arduino só escolhe a variante; `Needle: ...`, `No data` e
  `Homing...` são do Arduino.
- **Apps do topo:** somadas por app (os processos "Helper" contam para a
  app). Sem root, ~270 processos do sistema ficam de fora; são lembrados e
  saltados nos varrimentos seguintes. O próprio script fica de fora (com o PC
  parado aparecia sempre `Python`). Varrimento a cada 5 s, ~28 ms de CPU
  (antes ~44 ms a cada 3 s). O script todo gastava ~2,2% de um núcleo (≈0,3%
  do PC com 8 núcleos); o ciclo a 20 Hz sozinho são ~0,3%. Separar a RAM
  num intervalo próprio não compensa: no macOS sai da mesma chamada que o CPU.
- **Rede:** conta só a interface por onde sai o tráfego para a internet,
  escolhida pela rota (um `connect` UDP para `1.1.1.1` não envia nada) e não
  pelo nome, que muda de SO para SO (o Mac tem 18 interfaces: `lo0`,
  `utun*`, `awdl0`…). Com VPN conta a da VPN, uma só vez. Interface revista a
  cada 5 s (troca Wi-Fi ↔ cabo); velocidade lida a cada 1 s (~70 µs). Sem
  rede ou logo após trocar de interface mostra `↓--`. Unidades de 1000 bytes/s,
  como o Monitor de Atividade. O psutil não dá tráfego por processo, por isso
  não há "app do topo" na rede.
- **Sem dados 5 s** (ou `P`): o ponteiro estaciona no 0% e grava posição e
  fase na EEPROM, para não fazer homing no arranque seguinte.
- **Sem dados 1 minuto:** LCD e iluminação desligam. Com o ecrã desligado,
  um toque curto só o acende 10 s (não troca CPU/RAM).
- **Ecrã do computador a dormir:** o script manda `B0` e `P` e deixa de
  mandar leituras; retoma quando o ecrã acorda (verifica a cada 3 s).
  Deteção: macOS `CGDisplayIsAsleep` (ctypes); Linux `xset q` em X11,
  `/sys/class/drm/*/dpms` em Wayland/consola (não testado); outros sistemas
  assumem sempre ligado. Testar no Mac com `pmset displaysleepnow`.
- **O LCD só é escrito com o ponteiro parado:** escrever bloqueia alguns ms
  e podia fazer o motor perder passos a meio de um movimento.

### EEPROM
- Um registo de 4 bytes no endereço 0: posição (2), fase (1), `MAGIC` (1,
  escrito por último).
- Escreve-se ao estacionar; o `MAGIC` vai a 0 no primeiro movimento a
  seguir e no início de um homing. Lê-se só no arranque.
- Usa `update`: na prática só o `MAGIC` é reescrito (2 escritas por ciclo de
  estacionar/mexer, ~100 000 por byte → mais de 10 anos).

## Eletrónica da versão final

### Mapa de pinos (Nano)
| Pino | Função |
|---|---|
| D2, D3 | motor, bobina A (via driver) |
| D8, D9 | motor, bobina B (via driver) |
| D4 | botão CPU/RAM, a massa |
| D5, D6 | luzes de aviso (PWM, LED + 330 Ω direto ao pino) |
| D7, D12 | LCD RS, E |
| A0–A3 | LCD D4–D7 |
| D10 | iluminação: LCD + ponteiro + escala (PWM, via transístor) |
| D11 | botão Pomodoro (futuro) |
| D13 | piezo (futuro; `tone()` não interfere com o PWM do D10) |
| A4, A5, A6, A7 | livres (A4/A5 = I2C; A6/A7 só entrada analógica) |
| D0, D1 | **não usar**: são a série/USB |

### LCD
- Pinos: 1 GND, 2 5V, 3 V0 (contraste), 4 RS, 5 RW → GND, 6 E,
  11–14 D4–D7, 15 A (LED+), 16 K (LED−).
- **Modo de 4 bits:** cada byte vai em duas metades por D4–D7; os pinos 7–10
  do LCD (D0–D3) ficam desligados. Poupa 4 pinos do Arduino; o custo é
  ~200 µs por caractere em vez de ~100 µs. RW à massa: só se escreve, a
  biblioteca usa esperas fixas.
- **Se faltarem pinos:** adaptador **I²C (PCF8574)** soldado ao LCD. Usa só
  A4/A5 e liberta 6 pinos. Continua em 4 bits por dentro; mais lento (~0,5–1 ms
  por caractere, aguentável com a fila do `gauge.py`). Só liga/desliga a
  iluminação: para manter o brilho, tirar o jumper e deixá-la no D10.
  Biblioteca `LiquidCrystal_I2C`.
- **Modelo JHD162A, ROM A00** (japonesa), confirmado pela datasheet. Símbolos
  da ROM que não gastam slots: `0xDF` °, `0x7E` →, `0x7F` ←, `0xFF` bloco
  cheio, `0xA5` ponto central, `0xDB` □, `0xE4` µ, `0xF4` Ω, `0xE0` α,
  `0xE2` β, `0xF7` π, `0xF6` Σ, `0xE8` √, `0xF3` ∞, `0xFD` ÷, `0xE1` ä,
  `0xEF` ö, `0xF5` ü, `0xEE` ñ. **No ASCII, `\` aparece como ¥ e `~` como →.**
- O scroll do próprio LCD desloca as duas linhas juntas; não há desenho
  ponto a ponto (só os 8 slots). Para isso seria preciso um ecrã gráfico
  (OLED SSD1306 ou ST7920).
- **Retroiluminação:** o módulo **tem resistência na placa** (confirmado
  2026-10-04), por isso pode ligar-se sem resistência externa. Se for a
  habitual "101" (100 Ω), direto ao D10 dá ~20–30 mA: serve para testes, mas
  fica perto do limite do pino. Na versão final passa para o transístor da
  iluminação.
- **Contraste:** trimpot de 10 kΩ ("103"), multivoltas (tipo 3296) com
  parafuso por cima: pontas a 5V e GND, meio ao pino 3. Afinar com tudo
  montado, no ângulo de visão final. V0 aceita 0–5 V sem estragar (5 V =
  texto invisível); nunca acima de 5 V. Na breadboard está um de **7 kΩ**
  (2026-10-04): serve, o valor não é crítico.
- **Contraste a piscar quando o ponteiro mexe:** ruído do motor no 5V, não
  falta de corrente. Solução: 100 µF + 100 nF entre 5V e GND junto ao LCD
  (e junto ao driver do motor), massa do LCD num fio próprio até ao Nano.

### Iluminação (LED de uma cor)
- **Grupo de iluminação** (LCD, LEDs do ponteiro acrílico junto ao eixo,
  LEDs ou fita da escala): tudo num transístor **BC337** comandado pelo D10,
  para o brilho e o apagar do script valerem para tudo.
  - 5V → resistência → LED → coletor; emissor → GND; D10 → 1 kΩ → base;
    10 kΩ base → GND (apagado durante o reset).
  - **Cada LED com a sua resistência:** 150 Ω para branco/azul, 220 Ω para
    vermelho/âmbar/amarelo (~13–15 mA cada).
  - Aguenta 15–20 LEDs. Mais LEDs = mais ramos iguais em paralelo.
  - Para a escala, considerar **fita COB de 5 V** (luz contínua, sem pontos;
    já tem resistências; liga ao transístor como se fosse um LED).
    - **Tem de ser de 5 V**: muitas COB são de 12/24 V e a 5 V quase não acendem.
    - Largura: há de 2,7 / 3 / 5 / 8 mm; as finas de 5 V são menos comuns.
    - **Consumo alto:** tipicamente 5–10 W/m a 5 V (1–2 A/m). Ver no anúncio
      os W/m e o passo de corte. Ex.: 15 cm a 2 A/m ≈ 300 mA no máximo, o que
      leva o total para ~450 mA, quase no limite da USB. Saídas: brilho abaixo
      do máximo (o PWM reduz o consumo) ou fonte de 5 V à parte.
    - O BC337 aguenta 800 mA: chega para a fita mais os LEDs.
- **Luzes de aviso:** um LED por pino (D5/D6) com 330 Ω, sem transístor.
- LED: perna comprida = + (ânodo); lado achatado/perna curta = − (cátodo).
- WS2812/SK6812 (LEDs endereçáveis, um pino para todos) ficaram de fora:
  não se quer RGB.

### Orçamento de corrente (tudo pela USB, 500 mA)
| | aprox. |
|---|---|
| Nano + LCD + retroiluminação | ~40 mA |
| Motor (com driver) | ~80 mA |
| ~10 LEDs de iluminação | ~150 mA |
| 2 avisos | ~20 mA |
| **Total** | **~300 mA** |

Cabe, mas mais corrente baixa o 5V do Nano (díodo de entrada) e o motor
perde binário: repetir o self-test com a iluminação no máximo. Se faltar,
fonte de 5 V à parte para iluminação e motor, com massa comum.

## Material a comprar
**Essencial**
- 1 módulo **TB6612FNG** (driver do motor) — ou L293D + suporte DIP-16,
  com menos binário
- 2 transístores **BC337** (1 de reserva)
- 1 trimpot **10 kΩ multivoltas**, parafuso por cima (tipo 3296W)
- **Kit de resistências** sortidas (inclui 150, 220, 330 Ω, 1 kΩ e 10 kΩ)
- Condensadores: 3 × **100 µF** eletrolíticos (≥10 V), 4 × **100 nF**
  cerâmicos
- **LEDs** de 3 ou 5 mm na cor escolhida, 15–20 (para experimentar)
- 2 LEDs para os avisos (ex.: vermelho/âmbar)
- Placa perfurada (ou "expansion board" de terminais de parafuso para o
  Nano), barra de pinos fêmea para o Nano ficar amovível, fio rígido,
  manga termorretrátil

**Opcional / mais tarde**
- 1 m de fita **COB 5 V** na cor escolhida (escala)
- 1 **piezo passivo** (Pomodoro)
- 1 botão de pressão (Pomodoro)
- Breadboard e fios jumper, se não houver, para testar a iluminação

## Por fazer
1. ✅ Contar o curso em passos: **290** (medido duas vezes).
2. **Medir o ângulo real** entre batentes com transferidor (`-300` e `+300`
   no `step_counter`), para saber a resolução efetiva (graus/passo).
3. **Desenhar a escala** depois de 2. Decidir aí `POS_MIN`/`POS_MAX`.
4. Gerar o SVG do mostrador (script Python parametrizado: raio, ângulo de
   varrimento, nº de divisões). Imprimir a **100% / tamanho real**, com
   linha de controlo de 100 mm para verificar. Centro tem de ficar exato
   — 1 mm de desvio estraga a leitura.
5. ✅ Sketch final e script Python.
6. ✅ `HOME_PHASE = 6`. Procedimento (repetir se mudar a ligação do motor):
   com `step_counter` (s2): `d20`, `-300`, `+20`; marcar a ponta do
   ponteiro; `-` um passo de cada vez; a `phase` do primeiro `-` que já não
   mexe o ponteiro é a `HOME_PHASE`. Repetir para confirmar, pôr o valor em
   `gauge.ino` e mudar o `MAGIC` para forçar homing.
7. ✅ Resistência da retroiluminação do LCD: o módulo tem (2026-10-04).
8. Testar a iluminação na breadboard: transístor + LCD + 1 LED debaixo do
   ponteiro acrílico; acrescentar LEDs até ficar bem.
9. Passar o motor para o TB6612FNG, pôr os condensadores e recalibrar
   `HOME_PHASE`; repetir o self-test com a iluminação no máximo.
10. Testar o ecrã a dormir no Mac (`pmset displaysleepnow`).
11. Mais tarde: Pomodoro, luzes de aviso, temperatura (`°` = 0xDF do LCD).
    Temperatura: só o Linux tem via genérica (`psutil.sensors_temperatures()`).
    No Mac M1 (MacBook Air, sem ventoinha) só por API privada, sem sudo, via
    `ctypes` (como Stats/macmon): frágil entre chips e versões do macOS.
    No Windows só com o LibreHardwareMonitor a correr. Ideia: luz de aviso
    acima de ~95 °C em vez de ocupar o LCD.
12. **Ponteiro a estacionar encostado ao batente** sem homing (depois de um
    homing estaciona 12 meios passos acima, que é o certo): a posição na
    EEPROM está errada. Teste: homing forçado (botão premido ao ligar),
    várias sessões com carga e ver onde estaciona. Se descer de sessão para
    sessão, perde passos a subir: subir mais devagar (`MIN_UP_US`) ou
    TB6612FNG.
