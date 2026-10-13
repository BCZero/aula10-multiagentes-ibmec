"""
ETAPA 6 -- Os especialistas, cada um com UMA responsabilidade e SÓ as tools de que precisa.

ESQUELETO:
  ETAPA 6.1 -- criar_normas(), criar_escala(), criar_financeiro(), criar_redator()
  ETAPA 6.2 -- criar_coordenador(): usa Normas, Escala e Financeiro como TOOLS (as_tool)

As tools abaixo (PRONTAS) só repassam a chamada ao MCP Server do RH (cliente_mcp.chamar_mcp_async). Dar a cada agente
só as tools dele é o PRIVILÉGIO MÍNIMO (Etapa 9.1); o hook do 🆕 B (etapa10_governanca.py) confere isso em tempo de execução.

REFERÊNCIAS NAS AULAS
  aula4/ex03_agente_as_tool.py                         Coordenador que chama especialistas como tools    <- o mais parecido
  aula2/agente_handoff2.py                             as_tool com tool_name e tool_description
  aula4/ex02_handoff_roteamento.py                     handoff: compare (quem responde no fim?)
  aula7/exemplos/02_agentes_especializados/main.py     uma responsabilidade por agente
  aula7/agentes.py                                     ModelSettings(temperature=0) para respostas mais estáveis
  aula8/exemplos/06_excessive_agency/1_codigo_pronto/main.py   o problema de dar tools DEMAIS a um agente
"""
import json

from agents import Agent, ModelSettings, RunHooks, function_tool

from etapa05_cliente_mcp import chamar_mcp_async
from etapa04_ferramentas import cotacao_usd_brl

CONFIG = ModelSettings(temperature=0)


# ------------------------------------------------------------------ tools (PRONTAS)
@function_tool
async def consultar_normas(tema: str) -> str:
    """Regras da norma interna sobre um tema: férias, prazos, escala, abono, diária, aprovação, saúde ou dados."""
    return json.dumps(await chamar_mcp_async("consultar_normas", {"tema": tema}), ensure_ascii=False)


@function_tool
async def consultar_escala(equipe: str, inicio: str, fim: str) -> str:
    """Afastamentos da equipe que se sobrepõem ao período (AAAA-MM-DD) e o limite de afastados da regra N5."""
    return json.dumps(await chamar_mcp_async("consultar_escala", {"equipe": equipe, "inicio": inicio, "fim": fim}),
                      ensure_ascii=False)


@function_tool
async def tabela_diarias(tipo_destino: str) -> str:
    """Moeda e valor da diária para o tipo de destino: capital, interior ou exterior."""
    return json.dumps(await chamar_mcp_async("tabela_diarias", {"tipo_destino": tipo_destino}), ensure_ascii=False)


@function_tool
async def consultar_remuneracao(matricula: str) -> str:
    """[RESTRITA] Salário-base de um servidor. Só para calcular a venda de férias (N2)."""
    print(f"[tool] consultar_remuneracao({matricula}) EXECUTOU")  # o 🆕 B prova que, barrada, ela NÃO executa
    return json.dumps(await chamar_mcp_async("consultar_remuneracao", {"matricula": matricula}), ensure_ascii=False)


@function_tool
def cotacao_dolar() -> str:
    """Cotação atual do dólar em reais (USD->BRL). Devolve 'indisponível' se a API falhar: nunca invente o câmbio."""
    cotacao = cotacao_usd_brl()
    return f"{cotacao:.4f}" if cotacao else "indisponível"


# ------------------------------------------------------------------ ETAPA 6.1: especialistas
def criar_normas() -> Agent:
    return Agent(
        name="Normas",
        instructions=(
            "Você é o especialista em Normas de Recursos Humanos da Polícia Civil. "
            "Sua responsabilidade é analisar os pedidos e emitir parecer fundamentado sobre quais normas "
            "se aplicam (férias, prazos, abonos, diárias, etc.). Use a ferramenta consultar_normas. "
            "Não calcule valores monetários nem consulte a escala da equipe. "
            "Explique de forma objetiva e clara se há conformidade ou violação normativa."
        ),
        tools=[consultar_normas],
        model_settings=CONFIG,
    )


def criar_escala() -> Agent:
    return Agent(
        name="Escala",
        instructions=(
            "Você é o especialista em Escala de Trabalho do RH da Polícia Civil. "
            "Sua responsabilidade é consultar a escala da equipe (usando consultar_escala) e avaliar a regra N5 "
            "(limite de afastamentos simultâneos na equipe). "
            "Se houver conflito de escala, aponte os servidores já afastados e sugira um período alternativo "
            "viável com a mesma duração, iniciando em dia útil e permitido pelas regras."
        ),
        tools=[consultar_escala],
        model_settings=CONFIG,
    )


def criar_financeiro() -> Agent:
    return Agent(
        name="Financeiro",
        instructions=(
            "Você é o especialista Financeiro do RH da Polícia Civil. "
            "Sua responsabilidade é apurar os valores devidos para venda de férias (abono pecuniário, N2: "
            "dias_vendidos * salario_base / 30) e diárias de viagem a serviço (N7: 1 diária por pernoite mais meia "
            "diária no retorno, convertidas pela cotação do dólar para destinos internacionais). "
            "Utilize as ferramentas tabela_diarias, consultar_remuneracao e cotacao_dolar. "
            "Apresente sempre o cálculo detalhado. Se o câmbio estiver indisponível, registre 'a calcular'."
        ),
        tools=[tabela_diarias, consultar_remuneracao, cotacao_dolar],
        model_settings=CONFIG,
    )


def criar_redator() -> Agent:
    return Agent(
        name="Redator",
        instructions=(
            "Você é o Redator oficial do RH da Polícia Civil. "
            "Redija despachos formais de decisão (deferimento ou indeferimento) baseando-se exclusivamente "
            "nos fatos, dados e pareceres recebidos. Inclua matrícula, período, decisão fundamentada, regras citadas, "
            "valores calculados e sugestões de datas se houver. "
            "NUNCA mencione CPF nem dados de saúde ou CID (regra N10). "
            "Se receber apontamentos de revisão ou da chefia, ajuste o texto com precisão técnica."
        ),
        tools=[],
        model_settings=CONFIG,
    )


# ------------------------------------------------------------------ ETAPA 6.2: coordenador
def criar_coordenador(hooks: RunHooks | None = None, tools_extras: list | None = None) -> Agent:
    tools = [
        criar_normas().as_tool(
            tool_name="parecer_normas",
            tool_description="Consulta o especialista em Normas sobre regras aplicáveis e embasamento legal.",
            hooks=hooks,
            failure_error_function=None,
        ),
        criar_escala().as_tool(
            tool_name="parecer_escala",
            tool_description="Consulta o especialista em Escala sobre sobreposições e limites de afastamentos.",
            hooks=hooks,
            failure_error_function=None,
        ),
        criar_financeiro().as_tool(
            tool_name="parecer_financeiro",
            tool_description="Consulta o especialista Financeiro sobre valores de venda de férias ou diárias.",
            hooks=hooks,
            failure_error_function=None,
        ),
        *(tools_extras or []),
    ]
    return Agent(
        name="Coordenador",
        instructions=(
            "Você é o Coordenador da análise de pedidos de RH. "
            "Consulte os pareceres técnicos dos especialistas (Normas, Escala, Financeiro) através de suas "
            "respectivas ferramentas e consolide uma análise técnica coesa e conclusiva. "
            "Consulte apenas os especialistas pertinentes ao tipo de pedido."
        ),
        tools=tools,
        model_settings=CONFIG,
        hooks=hooks,
    )
