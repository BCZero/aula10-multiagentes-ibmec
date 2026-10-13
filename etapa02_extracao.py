"""
ETAPA 2 -- Extração estruturada do pedido (15 min) e 🆕 A -- votação em paralelo (OPCIONAL, bônus).

ESQUELETO: implemente as etapas na ordem (troque cada raise NotImplementedError pelo código da etapa).
  ETAPA 2.1 -- complete o modelo Pydantic `Pedido` (campos + Field(description=...))
  ETAPA 2.2 -- criar_extrator(): um Agent com output_type=Pedido
  ETAPA 2.3 -- extrair(): roda UM extrator e devolve um Pedido
  --- daqui para baixo é OPCIONAL (item 🆕 A, vale bônus). Pule se estiver sem tempo: o resto do sistema não depende dele.
  🆕 A.1    -- INSTRUCOES_VOTANTES: 3 instruções ligeiramente diferentes
  🆕 A.2    -- extrair_com_votacao(): os 3 extratores AO MESMO TEMPO (asyncio.gather)
  (PRONTO)  -- votar(): maioria por campo; sem maioria em tipo/datas -> divergencia=True
  (PRONTO)  -- no __main__: o tempo em paralelo x em sequência

REFERÊNCIAS NAS AULAS
  aula2/agente_output.py                   output_type com um BaseModel simples
  aula2/agente_output2.py                  Enum + listas + Field(description=...)          <- o mais parecido
  aula7/exemplos/08_paralelismo/main.py    🆕 A: o CONCEITO de paralelismo (lá: tarefas DIFERENTES para ganhar tempo;
                                           aqui: a MESMA tarefa 3 vezes para ganhar confiabilidade)
  aula3/agente_parada3.py                  Runner.run (assíncrono) dentro de asyncio, como em A.2
  aula3/agente_retry.py                    por que a data de hoje entra nas instruções (já feito abaixo, com texto_hoje())

Rodar:
    python etapa02_extracao.py            # extrai C1, C2, C5 e C11
    python etapa02_extracao.py --votacao  # 🆕 A: C1, C2 e C9 com 3 votos e o tempo medido

Pronto quando: C2 sai com dias_vendidos=10, C11 com tipo_destino="exterior", C5 com matricula=None;
               na votação, C1/C2 unânimes e C9 mostrando os votos.
"""
import asyncio
import sys
import time
from collections import Counter
from enum import Enum

from agents import Agent, Runner
from pydantic import BaseModel, Field

from dados.casos import CASOS
from etapa04_ferramentas import texto_hoje
from provedor import configurar

MODELO = configurar()


class TipoPedido(str, Enum):
    FERIAS = "ferias"
    ABONO = "abono"
    DIARIA = "diaria"
    LICENCA_SAUDE = "licenca_saude"
    OUTRO = "outro"


class TipoDestino(str, Enum):
    CAPITAL = "capital"
    INTERIOR = "interior"
    EXTERIOR = "exterior"


class Pedido(BaseModel):
    tipo: TipoPedido = Field(
        description=(
            "Tipo do pedido: 'ferias' (descanso anual, pode incluir venda de dias), 'abono' (folga de 1 dia útil), "
            "'diaria' (viagem a serviço), 'licenca_saude' (afastamento por doença) ou 'outro'."
        )
    )
    matricula: str | None = Field(
        default=None,
        description="Matrícula do servidor (4 dígitos numéricos, ex: '1002'). Retorne null se o servidor não informou a matrícula."
    )
    data_inicio: str | None = Field(
        default=None,
        description="Data de início do período no formato AAAA-MM-DD. Retorne null se o servidor não informou o dia exato específico."
    )
    data_fim: str | None = Field(
        default=None,
        description="Data de término do período no formato AAAA-MM-DD. Para abono, igual à data_inicio. Retorne null se não informada data exata."
    )
    dias_vendidos: int = Field(
        default=0,
        ge=0,
        le=10,
        description="Quantidade de dias de férias vendidos/convertidos em abono pecuniário (0 a 10 dias). Padrão 0. Preencha apenas se explicitamente solicitado."
    )
    destino: str | None = Field(
        default=None,
        description="Cidade ou local de destino da viagem a serviço (apenas para pedidos de diária). Retorne null se não aplicável."
    )
    tipo_destino: TipoDestino | None = Field(
        default=None,
        description="Classificação do destino da viagem de diária: 'capital', 'interior' ou 'exterior'. Retorne null se não for diária."
    )
    justificativa: str = Field(
        default="",
        description="Resumo do motivo ou justificativa do pedido apresentado pelo servidor, omitindo dados de saúde confidenciais."
    )


def criar_extrator(instrucoes: str, input_guardrails: list | None = None) -> Agent:
    return Agent(
        name="Extrator",
        instructions=instrucoes,
        output_type=Pedido,
        input_guardrails=input_guardrails or [],
    )


INSTRUCOES_EXTRATOR = (
    f"Hoje é {texto_hoje()}. "
    "Você extrai pedidos de servidores públicos para o RH (férias, abono ou diária). "
    "Analise o texto e preencha todos os campos do modelo Pedido. "
    "Datas devem estar estritamente no formato AAAA-MM-DD. Se o servidor não informou o dia exato específico "
    "(por exemplo, apenas mês ou ano, ou período vago), use null para data_inicio e data_fim; NUNCA invente datas. "
    "Para abono, data_fim deve ser igual a data_inicio. "
    "Matrícula possui 4 dígitos (ex: '1001'); se não informada, use null. "
    "Dias vendidos referem-se à conversão de férias em abono pecuniário (0 a 10 dias); registre apenas se explicitamente pedido (padrão 0). "
    "Para diárias, identifique destino e tipo_destino ('capital', 'interior' ou 'exterior'). "
    "Não inclua dados médicos ou diagnósticos na justificativa."
)


def extrair(texto: str) -> Pedido:
    extrator = criar_extrator(INSTRUCOES_EXTRATOR)
    resultado = Runner.run_sync(extrator, texto)
    return resultado.final_output


# ------------------------------------------------------------------ 🆕 A: votação (OPCIONAL, bônus)
# Para ligar no grafo depois de pronto: USAR_VOTACAO = True em etapa07_grafo.py.
INSTRUCOES_VOTANTES: list[str] = [
    (
        f"Hoje é {texto_hoje()}. "
        "Você é um extrator literal de RH. Extraia estritamente o que foi dito no pedido sem fazer suposições. "
        "Datas devem ser AAAA-MM-DD ou null se vagas. Matrícula com 4 dígitos ou null. Dias vendidos entre 0 e 10 (padrão 0)."
    ),
    (
        f"Hoje é {texto_hoje()}. "
        "Você é um analista de RH rigoroso com datas e prazos. Extraia os dados do pedido atentando para conformidade. "
        "Não invente datas; se não houver dia exato, use null. Dias vendidos só se solicitado expressamente."
    ),
    (
        f"Hoje é {texto_hoje()}. "
        "Você é um especialista em normas de gestão de pessoas da PCDF. Extraia com precisão tipo, matrícula, datas e diárias. "
        "Se o pedido for vago ou faltar dia específico, marque as datas como null. Padrão de dias vendidos é 0."
    ),
]


async def extrair_com_votacao(texto: str) -> tuple[Pedido, bool, list[Pedido]]:
    extratores = [criar_extrator(instrucoes) for instrucoes in INSTRUCOES_VOTANTES]
    resultados = await asyncio.gather(*(Runner.run(e, texto) for e in extratores))
    votos = [r.final_output for r in resultados]
    pedido_final, divergencia = votar(votos)
    return pedido_final, divergencia, votos


def votar(votos: list[Pedido]) -> tuple[Pedido, bool]:
    """(PRONTO) Maioria por campo. Sem maioria (2 de 3) em `tipo` ou nas datas -> divergencia=True: o sistema vai
    pedir esclarecimento em vez de adivinhar. Os campos não votados vêm do primeiro voto."""
    decidido, divergencia = {}, False
    for campo in ("tipo", "matricula", "data_inicio", "data_fim", "dias_vendidos"):
        valor, quantos = Counter(getattr(voto, campo) for voto in votos).most_common(1)[0]
        decidido[campo] = valor
        if quantos < 2 and campo in ("tipo", "data_inicio", "data_fim"):
            divergencia = True
    return votos[0].model_copy(update=decidido), divergencia


def _demo_extracao() -> None:
    for nome in ("C1_abono_simples", "C2_ferias_com_venda", "C5_sem_matricula", "C11_diaria_exterior"):
        pedido = extrair(CASOS[nome]["pedido"])
        print(f"\n{nome}\n  {pedido.model_dump()}")


def _demo_votacao() -> None:
    for nome in ("C1_abono_simples", "C2_ferias_com_venda", "C9_divergencia"):
        texto = CASOS[nome]["pedido"]
        inicio = time.perf_counter()
        pedido, divergencia, votos = asyncio.run(extrair_com_votacao(texto))
        segundos = time.perf_counter() - inicio
        print(f"\n{nome}  ({segundos:.1f}s, divergencia={divergencia})")
        for i, voto in enumerate(votos, 1):
            print(f"  voto {i}: tipo={voto.tipo.value} {voto.data_inicio}..{voto.data_fim} vende={voto.dias_vendidos}")
        print(f"  decisão: {pedido.model_dump()}")
    # (PRONTO) os mesmos 3 extratores EM SEQUÊNCIA, para comparar: o paralelo fica perto do tempo de UMA chamada?
    texto = CASOS["C2_ferias_com_venda"]["pedido"]
    inicio = time.perf_counter()
    for instrucoes in INSTRUCOES_VOTANTES:
        Runner.run_sync(criar_extrator(instrucoes), texto)
    print(f"\nC2 em sequência: {time.perf_counter() - inicio:.1f}s (compare com o tempo do C2 em paralelo, acima)")


if __name__ == "__main__":
    print(f"[modelo] {MODELO}")
    _demo_votacao() if "--votacao" in sys.argv else _demo_extracao()
