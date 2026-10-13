"""
ETAPA 5 -- MCP Server do RH (30 min). Volta aqui na ETAPA 9 (token, regra N8 e auditoria).

Os dados saem de dentro do agente e passam a morar num PROCESSO SEPARADO, que fala MCP. O agente (Etapa 5.3) e os
nós do grafo (Etapa 7, via cliente_mcp.chamar_mcp) só enxergam as tools abaixo: nunca os CSVs.

ESQUELETO: implemente as etapas na ordem (troque cada raise NotImplementedError pelo código da etapa).
  ETAPA 5.1 -- as 5 tools de LEITURA: declare cada uma com @mcp.tool(), escreva a DOCSTRING (é o "schema" que o LLM
               lê) e devolva o resultado da consulta pronta de dados_rh.py. Toda tool devolve UM dict.
  ETAPA 5.2 -- a tool de ESCRITA registrar_decisao, IDEMPOTENTE (protocolo repetido não duplica)
  ETAPA 9.2 -- autorização NO SERVER: token + regra N8 (chefia certa, nunca o próprio servidor) + decisão num enum
  ETAPA 9.3 -- auditoria: toda tentativa de escrita (ALLOW ou DENY) vira uma linha em saidas/auditoria.jsonl

REFERÊNCIAS NAS AULAS
  aula6/desafio2/mcp_unidades.py               MCP Server que lê um CSV                          <- o mais parecido (5.1)
  aula5/exemplos/03_mcp_parametros/server.py   tool com parâmetros opcionais
  aula5/exemplos/05_multiplas_tools/server.py  várias tools no mesmo server, tool de "descoberta"
  aula3/agente_retry3.py                       idempotência (5.2)
  aula5/exemplos/10_seguranca/server_seguro.py token, enum fechado e auditoria NO SERVER        <- o mais parecido (9.2/9.3)

Testar sem agente (MCP Inspector, precisa de Node.js):
    npx @modelcontextprotocol/inspector python etapa05_mcp_rh.py

Pronto quando: o Inspector lista as 6 tools; registrar C1 duas vezes gera um único registro.
"""
import json
import os
from datetime import datetime

from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer

from dados_rh import PASTA, SAIDAS, carregar_servidores, diaria, escala_da_equipe, normas, remuneracao, servidor_publico

# O server roda como SUBPROCESSO: ele não herda o ambiente de quem o chamou. Por isso carrega o .env sozinho
# (mesmo cuidado de aula5/exemplos/02_mcp_basico/database.py).
load_dotenv(PASTA / ".env")

mcp = MCPServer("rh-mcp")

ARQUIVO_REGISTROS = SAIDAS / "registros.json"
ARQUIVO_AUDITORIA = SAIDAS / "auditoria.jsonl"
TOKEN_REGISTRO = os.getenv("TOKEN_REGISTRO", "")
DECISOES_VALIDAS = {"deferido", "indeferido", "indeferido_pela_chefia"}
TIPOS_COM_CHEFIA = {"ferias", "diaria"}  # regra N8


def _ler_registros() -> dict:
    """(PRONTO) {protocolo: registro} gravados até agora."""
    if ARQUIVO_REGISTROS.exists():
        return json.loads(ARQUIVO_REGISTROS.read_text(encoding="utf-8"))
    return {}


def _gravar_registros(registros: dict) -> None:
    """(PRONTO)"""
    ARQUIVO_REGISTROS.write_text(json.dumps(registros, ensure_ascii=False, indent=2), encoding="utf-8")


# ------------------------------------------------------------------ ETAPA 5.1: leitura
@mcp.tool()
def consultar_servidor(matricula: str) -> dict:
    """Consulta os dados cadastrais públicos de um servidor pelo número da matrícula (sem salário-base).

    Args:
        matricula: Matrícula do servidor (ex: '1002').
    """
    return servidor_publico(matricula)


@mcp.tool()
def consultar_escala(equipe: str, inicio: str, fim: str) -> dict:
    """Consulta afastamentos da equipe no período informado e o limite de afastados simultâneos (regra N5).

    Args:
        equipe: Nome da equipe (ex: 'Cartório', 'Plantão A').
        inicio: Data de início no formato AAAA-MM-DD.
        fim: Data de término no formato AAAA-MM-DD.
    """
    return escala_da_equipe(equipe, inicio, fim)


@mcp.tool()
def consultar_normas(tema: str) -> dict:
    """Consulta as regras da Norma Interna de Gestão de Pessoas por tema.

    Temas disponíveis: 'férias', 'prazos', 'escala', 'abono', 'diária', 'aprovação', 'saúde', 'dados'.

    Args:
        tema: Tema ou assunto da norma a consultar.
    """
    return {"tema": tema, "regras": normas(tema)}


@mcp.tool()
def tabela_diarias(tipo_destino: str) -> dict:
    """Consulta a moeda e o valor unitário da diária de acordo com o tipo de destino.

    Args:
        tipo_destino: Classificação do destino ('capital', 'interior' ou 'exterior').
    """
    return diaria(tipo_destino)


@mcp.tool()
def consultar_remuneracao(matricula: str) -> dict:
    """[RESTRITA ao Financeiro] Consulta o salário-base de um servidor para cálculo de abono pecuniário.

    Args:
        matricula: Matrícula do servidor (ex: '1002').
    """
    return remuneracao(matricula)


# ------------------------------------------------------------------ ETAPA 5.2 / 9.2 / 9.3: escrita
def _auditar(protocolo: str, decisao_acesso: str, motivo: str, parametros: dict) -> None:
    """Registra uma tentativa de gravação (ALLOW ou DENY) em saidas/auditoria.jsonl."""
    params_audit = {k: v for k, v in parametros.items() if k not in ("token", "despacho")}
    registro = {
        "quando": datetime.now().isoformat(),
        "thread_id": parametros.get("thread_id", ""),
        "quem": parametros.get("aprovador", ""),
        "tool": "registrar_decisao",
        "protocolo": protocolo,
        "decisao_acesso": decisao_acesso,
        "motivo": motivo,
        "parametros": params_audit,
    }
    SAIDAS.mkdir(parents=True, exist_ok=True)
    with open(ARQUIVO_AUDITORIA, "a", encoding="utf-8") as f:
        f.write(json.dumps(registro, ensure_ascii=False) + "\n")


@mcp.tool()
def registrar_decisao(protocolo: str, matricula: str, tipo: str, inicio: str, fim: str, decisao: str,
                      aprovador: str, despacho: str, token: str, thread_id: str = "") -> dict:
    """[ESCRITA -- requer autorização] Registra formalmente a decisão sobre um pedido de forma idempotente.

    Args:
        protocolo: Protocolo único e idempotente do pedido gerado por hash dos dados.
        matricula: Matrícula do servidor solicitante.
        tipo: Tipo do pedido ('ferias', 'abono', 'diaria').
        inicio: Data inicial do período (AAAA-MM-DD).
        fim: Data final do período (AAAA-MM-DD).
        decisao: Decisão final ('deferido', 'indeferido', 'indeferido_pela_chefia').
        aprovador: Identificação de quem autorizou ('sistema' ou matrícula da chefia).
        despacho: Texto fundamentado do despacho da decisão.
        token: Token secreto de autorização de escrita no MCP server.
        thread_id: Identificador opcional da thread de execução do fluxo.
    """
    parametros = {"matricula": matricula, "tipo": tipo, "inicio": inicio, "fim": fim, "decisao": decisao,
                  "aprovador": aprovador, "thread_id": thread_id}

    # ETAPA 9.2 -- Validação de autorização no server
    if not TOKEN_REGISTRO or token != TOKEN_REGISTRO:
        _auditar(protocolo, "DENY", "token inválido", parametros)
        return {"sucesso": False, "erro": "token inválido"}

    if decisao not in DECISOES_VALIDAS:
        _auditar(protocolo, "DENY", f"decisão inválida ({decisao})", parametros)
        return {"sucesso": False, "erro": f"decisão inválida ({decisao})"}

    if str(aprovador) == str(matricula):
        _auditar(protocolo, "DENY", "autoaprovação (N8)", parametros)
        return {"sucesso": False, "erro": "autoaprovação (N8)"}

    if decisao == "deferido" and tipo in TIPOS_COM_CHEFIA:
        servidores = carregar_servidores()
        serv = servidores.get(str(matricula))
        chefia_esperada = str(serv.get("chefia")) if serv else None
        if str(aprovador) != chefia_esperada:
            _auditar(protocolo, "DENY", f"aprovador não é a chefia {chefia_esperada} (N8)", parametros)
            return {"sucesso": False, "erro": f"aprovador não é a chefia {chefia_esperada} (N8)"}

    # ETAPA 5.2 -- Idempotência e gravação
    registros = _ler_registros()
    if protocolo in registros:
        _auditar(protocolo, "ALLOW", "duplicado", parametros)
        return {"sucesso": True, "duplicado": True, "protocolo": protocolo, "registro": registros[protocolo]}

    dados_registro = {**parametros, "despacho": despacho, "registrado_em": datetime.now().isoformat()}
    registros[protocolo] = dados_registro
    _gravar_registros(registros)
    _auditar(protocolo, "ALLOW", "gravado", parametros)
    return {"sucesso": True, "protocolo": protocolo}


if __name__ == "__main__":
    mcp.run(transport="stdio")
