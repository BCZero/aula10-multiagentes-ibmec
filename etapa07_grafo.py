"""
ETAPAS 7, 8, 9 e 10 (e o opcional 🆕 C) -- O sistema como GRAFO (LangGraph), com persistência, humanos no fluxo e segurança.

Reaproveite TUDO o que você já fez: cada nó só chama funções e agentes das etapas anteriores e devolve O QUE MUDOU
no estado (mais o próprio nome em "caminho").

ESQUELETO: implemente as etapas na ordem (troque cada raise NotImplementedError pelo código da etapa).
  ETAPA 7.1 -- confira o Estado (o reducer de `violacoes`)
  ETAPA 7.2 -- nós e roteadores da ENTRADA: proteger_entrada, extrair, identificar_servidor, calcular_periodo
  ETAPA 7.3 -- PARALELISMO: normas, escala e financeiro (fan-out) e consolidar (fan-in)
  ETAPA 7.4 -- CICLO: redigir_despacho <-> validar, no máximo 3 tentativas
  ETAPA 7.5 -- registrar (MCP) e construir(): nós, arestas fixas e condicionais
  ETAPA 8.1 -- compilar com o checkpointer SQLite (já está em main(); confira)
  ETAPA 8.2 -- aprovacao_chefia com interrupt()
  ETAPA 8.3 -- rotear_chefia: aprovado / rejeitado (máx. 2) / indeferido_pela_chefia
  ETAPA 8.4 -- retomar() com Command(resume=...) (o listar_pendentes() já vem pronto: leia)
  ETAPA 9.4 -- guardrail de SAÍDA no registrar (despacho sem CPF/CID, N10)
  (ETAPA 9.2/9.3 ficam no etapa05_mcp_rh.py; ETAPA 10 em etapa10_observabilidade.py e etapa10_governanca.py)
  --- OPCIONAIS (bônus): o grafo funciona sem eles. Faça só se sobrar tempo, depois da Etapa 9.
  🆕 A     -- ligue USAR_VOTACAO = True (depois de fazer o 🆕 A em etapa02_extracao.py)
  🆕 C.1   -- proteger_entrada com DUAS faixas (ok / dúvida / bloqueado)
  🆕 C.2   -- triagem_humana com interrupt() e rotear_triagem; depois ligue USAR_TRIAGEM_HUMANA = True

REFERÊNCIAS NAS AULAS
  aula6/exemplos/05_multiplos_nos/main.py          nós devolvem só o que mudou
  aula6/exemplos/06_fluxo_condicional/main.py      add_conditional_edges com roteador                      (7.2)
  aula6/exemplos/07_dag/main.py                    ramifica e converge                                     (7.3)
  aula7/exemplos/08_paralelismo/main.py            fan-out / fan-in por LISTA e o conflito de escrita      (7.3)
  aula6/exemplos/08_fluxo_ciclico/main.py          ciclo com condição de parada + recursion_limit          (7.4)
  aula6/exemplos/11_grafo_real/main.py             nós de LLM, MCP, API de feriados e tool local, num DAG + ciclo
  aula6/desafio2/main.py                           LLM + MCP + API + ciclo, com caminho percorrido         <- o mais parecido (7)
  aula7/exemplos/05_equipe_multiagente/main.py     especialistas como NÓS (aqui não há mais Coordenador)
  aula8/exemplos/10_agentes_e_grafo/1_codigo_pronto/main.py   agente decide, grafo controla
  aula7/exemplos/04_persistencia/main.py           SqliteSaver, thread_id, get_state, retomar em outro processo  (8.1)
  aula7/exemplos/06_human_in_the_loop/main.py      interrupt() e Command(resume=...)                       (8.2/8.4, 🆕 C)
  aula7/exemplos/07_aprovacao_revisao/main.py      rejeição com feedback e limite de tentativas            <- o mais parecido (8.3)
  aula7/desafio2/                                  duas pessoas decidindo no mesmo fluxo                   (🆕 C)
  aula8/exemplos/05_guardrail_saida/1_codigo_pronto/main.py   conferir a saída antes de entregar         (9.4)
  aula6/exemplos/04_langgraph_basico/main.py       app.get_graph().draw_mermaid()

Rodar (a partir de aula10/):
    python etapa07_grafo.py C2_ferias_com_venda           # um caso (o nome do caso é o thread_id)
    python etapa07_grafo.py --todos                       # os 12 casos
    python etapa07_grafo.py --pendentes                   # quem está pausado e onde (pronto)
    python etapa07_grafo.py --retomar C2_ferias_com_venda sim 1010             # a chefia 1010 aprova
    python etapa07_grafo.py --retomar C2_ferias_com_venda "nao:mude as datas" 1010
    python etapa07_grafo.py --retomar C10_zona_cinzenta "sim:tratar como abono" rh   # 🆕 C (opcional): o atendente do RH decide
    python etapa07_grafo.py --sem-guardrail C7_autoaprovacao   # ETAPA 9.5: o server ainda nega?
    python etapa07_grafo.py --mermaid                     # o diagrama
"""
import operator
import os
import re
import sys
from typing import Annotated, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

import etapa06_agentes as agentes
from etapa05_cliente_mcp import chamar_mcp
from dados.casos import CASOS
from dados_rh import SAIDAS
from etapa02_extracao import extrair, extrair_com_votacao
from etapa04_ferramentas import gerar_protocolo, rodar_com_limites_sync
from etapa03_guardrails import LIMIAR, MENSAGENS, encontrar_dado_sensivel, nota_escopo
from etapa10_observabilidade import medido
from provedor import configurar
from etapa06_regras import precisa_chefia, resumo_periodo, violacoes_objetivas

MODELO = configurar()

MAX_TENTATIVAS = 3     # ciclo redigir <-> validar
MAX_REJEICOES = 2      # ciclo chefia -> redigir
USAR_VOTACAO = False          # 🆕 A (opcional): True usa os 3 extratores com votação (etapa02_extracao.py)
USAR_TRIAGEM_HUMANA = False   # 🆕 C (opcional): True liga as duas faixas e o nó triagem_humana
LIMIAR_OK = 0.4        # 🆕 C: abaixo disto, segue direto
LIMIAR_BLOQUEIO = 0.8  # 🆕 C: a partir disto, bloqueia; entre os dois, um humano decide
GUARDRAIL_LIGADO = "--sem-guardrail" not in sys.argv  # ETAPA 9.5


# ------------------------------------------------------------------ ETAPA 7.1: o estado
class Estado(TypedDict, total=False):
    thread_id: str
    texto: str
    bloqueado: bool
    motivo_bloqueio: str
    nota_escopo: float
    triagem: dict                 # 🆕 C: {"segue": bool, "orientacao": str, "quem": str}
    pedido: dict                  # Pedido.model_dump(mode="json")
    divergencia: bool             # 🆕 A
    servidor: dict
    periodo: dict
    escala: dict
    parecer_normas: str
    parecer_escala: str
    parecer_financeiro: str
    violacoes: Annotated[list[str], operator.add]
    decisao: str                  # "deferido" | "indeferido" | "indeferido_pela_chefia"
    despacho: str
    tentativas: int
    erro_validacao: str
    rejeicoes: int
    feedback_chefia: str
    aprovador: str
    protocolo: str
    resultado: str                # o desfecho, para a tabela da Etapa 11
    caminho: Annotated[list[str], operator.add]   # cada nó só ACRESCENTA o próprio nome


# ------------------------------------------------------------------ nós PRONTOS
def receber(estado: Estado) -> dict:
    return {"texto": estado["texto"].strip(), "tentativas": 0, "rejeicoes": 0, "violacoes": [],
            "erro_validacao": "", "feedback_chefia": "", "caminho": ["receber"]}


def recusar(estado: Estado) -> dict:
    orientacao = (estado.get("triagem") or {}).get("orientacao")
    return {"resultado": "recusado", "despacho": orientacao or estado.get("motivo_bloqueio", ""), "caminho": ["recusar"]}


def pedir_dados(estado: Estado) -> dict:
    faltam = "a matrícula e as datas exatas (AAAA-MM-DD)"
    if estado.get("divergencia"):
        faltam = "as datas e o tipo do pedido (a leitura do seu texto ficou ambígua)"
    return {"resultado": "pediu_dados", "despacho": f"Para seguir, informe {faltam}.", "caminho": ["pedir_dados"]}


# ------------------------------------------------------------------ ETAPA 7.2: entrada
def proteger_entrada(estado: Estado) -> dict:
    texto = estado["texto"]
    if not GUARDRAIL_LIGADO:
        return {"bloqueado": False, "nota_escopo": 0.0, "caminho": ["proteger_entrada"]}
    sensivel = encontrar_dado_sensivel(texto)
    if sensivel:
        return {
            "bloqueado": True,
            "motivo_bloqueio": MENSAGENS.get(sensivel, MENSAGENS["fora_do_escopo"]),
            "caminho": ["proteger_entrada"],
        }
    nota, motivo = nota_escopo(texto)
    if USAR_TRIAGEM_HUMANA:
        if nota >= LIMIAR_BLOQUEIO:
            return {"bloqueado": True, "motivo_bloqueio": MENSAGENS["fora_do_escopo"], "nota_escopo": nota, "caminho": ["proteger_entrada"]}
        elif nota >= LIMIAR_OK:
            return {"bloqueado": False, "nota_escopo": nota, "caminho": ["proteger_entrada"]}
        else:
            return {"bloqueado": False, "nota_escopo": nota, "caminho": ["proteger_entrada"]}
    else:
        if nota >= LIMIAR:
            return {"bloqueado": True, "motivo_bloqueio": MENSAGENS["fora_do_escopo"], "nota_escopo": nota, "caminho": ["proteger_entrada"]}
        return {"bloqueado": False, "nota_escopo": nota, "caminho": ["proteger_entrada"]}


def rotear_entrada(estado: Estado) -> str:
    if estado.get("bloqueado"):
        return "bloqueado"
    if USAR_TRIAGEM_HUMANA:
        nota = estado.get("nota_escopo", 0.0)
        if LIMIAR_OK <= nota < LIMIAR_BLOQUEIO:
            return "duvida"
    return "ok"


def triagem_humana(estado: Estado) -> dict:
    resposta = interrupt({
        "pergunta": "Este texto deve seguir como pedido ao RH?",
        "texto": estado["texto"],
        "nota": estado.get("nota_escopo"),
        "quem_decide": "atendente do RH",
    })
    segue = resposta.get("segue", False) if isinstance(resposta, dict) else False
    orientacao = resposta.get("orientacao", "") if isinstance(resposta, dict) else ""
    quem = resposta.get("quem", "rh") if isinstance(resposta, dict) else "rh"
    novo_texto = estado["texto"]
    if segue and orientacao:
        novo_texto = f"{novo_texto}\n[RH: {orientacao}]"
    return {
        "triagem": {"segue": segue, "orientacao": orientacao, "quem": quem},
        "texto": novo_texto,
        "caminho": ["triagem_humana"],
    }


def rotear_triagem(estado: Estado) -> str:
    triagem = estado.get("triagem") or {}
    return "segue" if triagem.get("segue") else "recusa"


def no_extrair(estado: Estado) -> dict:
    texto = estado["texto"]
    if USAR_VOTACAO:
        import asyncio
        pedido, divergencia, votos = asyncio.run(extrair_com_votacao(texto))
    else:
        pedido = extrair(texto)
        divergencia = False
    return {
        "pedido": pedido.model_dump(mode="json"),
        "divergencia": divergencia,
        "caminho": ["extrair"],
    }


def rotear_extracao(estado: Estado) -> str:
    if estado.get("divergencia"):
        return "incompleto"
    p = estado.get("pedido") or {}
    if not p.get("matricula") or not p.get("data_inicio") or not p.get("data_fim"):
        return "incompleto"
    if p.get("tipo") in ("licenca_saude", "outro"):
        return "incompleto"
    return "ok"


def identificar_servidor(estado: Estado) -> dict:
    matricula = estado["pedido"].get("matricula")
    servidor = chamar_mcp("consultar_servidor", {"matricula": matricula})
    return {"servidor": servidor, "caminho": ["identificar_servidor"]}


def rotear_servidor(estado: Estado) -> str:
    s = estado.get("servidor") or {}
    if s.get("erro") or not s.get("nome"):
        return "nao_encontrado"
    return "ok"


def calcular_periodo(estado: Estado) -> dict:
    p = estado["pedido"]
    s = estado["servidor"]
    periodo = resumo_periodo(p["data_inicio"], p["data_fim"])
    escala = chamar_mcp("consultar_escala", {
        "equipe": s.get("equipe", ""),
        "inicio": p["data_inicio"],
        "fim": p["data_fim"],
    })
    return {
        "periodo": periodo,
        "escala": escala,
        "fonte_feriados": periodo.get("fonte_feriados", ""),
        "caminho": ["calcular_periodo"],
    }


# ------------------------------------------------------------------ ETAPA 7.3: paralelismo
def _fatos(estado: Estado) -> str:
    """(PRONTO) O que os especialistas recebem: pedido, servidor (sem salário), período e escala."""
    return (f"PEDIDO: {estado['pedido']}\nSERVIDOR: {estado['servidor']}\n"
            f"PERÍODO: {estado['periodo']}\nESCALA DA EQUIPE: {estado['escala']}")


def no_normas(estado: Estado) -> dict:
    agente = agentes.criar_normas()
    parecer = rodar_com_limites_sync(agente, _fatos(estado)).final_output
    return {"parecer_normas": parecer, "caminho": ["normas"]}


def no_escala(estado: Estado) -> dict:
    agente = agentes.criar_escala()
    parecer = rodar_com_limites_sync(agente, _fatos(estado)).final_output
    return {"parecer_escala": parecer, "caminho": ["escala"]}


def no_financeiro(estado: Estado) -> dict:
    p = estado["pedido"]
    if p.get("tipo") == "abono" or (p.get("tipo") == "ferias" and not p.get("dias_vendidos")):
        parecer = "Não se aplica cálculo financeiro (sem venda de dias ou diárias)."
    else:
        agente = agentes.criar_financeiro()
        parecer = rodar_com_limites_sync(agente, _fatos(estado)).final_output
    return {"parecer_financeiro": parecer, "caminho": ["financeiro"]}


def consolidar(estado: Estado) -> dict:
    violacoes = violacoes_objetivas(estado["pedido"], estado["servidor"], estado["periodo"], estado["escala"])
    decisao = "indeferido" if violacoes else "deferido"
    return {
        "violacoes": violacoes,
        "decisao": decisao,
        "caminho": ["consolidar"],
    }


# ------------------------------------------------------------------ ETAPA 7.4: ciclo
def redigir_despacho(estado: Estado) -> dict:
    fatos = (
        f"{_fatos(estado)}\n"
        f"DECISÃO: {estado['decisao']}\n"
        f"VIOLAÇÕES: {estado.get('violacoes', [])}\n"
        f"PARECER NORMAS:\n{estado.get('parecer_normas', '')}\n"
        f"PARECER ESCALA:\n{estado.get('parecer_escala', '')}\n"
        f"PARECER FINANCEIRO:\n{estado.get('parecer_financeiro', '')}\n"
    )
    if estado.get("erro_validacao"):
        fatos += f"\nCORREÇÃO EXIGIDA PELA VALIDAÇÃO: {estado['erro_validacao']}"
    if estado.get("feedback_chefia"):
        fatos += f"\nMOTIVO DA REJEIÇÃO PELA CHEFIA: {estado['feedback_chefia']}"

    redator = agentes.criar_redator()
    despacho = rodar_com_limites_sync(redator, fatos).final_output
    return {"despacho": despacho, "caminho": ["redigir_despacho"]}


def problemas_do_despacho(despacho: str, pedido: dict) -> list[str]:
    """(PRONTO) O que falta ou sobra no despacho: precisa citar a matrícula, o início do período (AAAA-MM-DD ou
    DD/MM/AAAA) e a decisão, e não pode ter CPF nem CID (N10). Lista vazia = despacho válido."""
    problemas, texto = [], despacho.lower()
    if pedido.get("matricula") and pedido["matricula"] not in despacho:
        problemas.append(f"cite a matrícula {pedido['matricula']}")
    inicio = pedido.get("data_inicio") or ""
    if inicio and inicio not in despacho and "/".join(reversed(inicio.split("-"))) not in despacho:
        problemas.append(f"cite o início do período ({inicio})")
    if "deferid" not in texto and "indeferid" not in texto:
        problemas.append("diga a decisão (deferido ou indeferido)")
    if encontrar_dado_sensivel(despacho):
        problemas.append("tire o CPF/CID do despacho (N10)")
    return problemas


def validar(estado: Estado) -> dict:
    problemas = problemas_do_despacho(estado.get("despacho", ""), estado.get("pedido", {}))
    return {
        "tentativas": estado.get("tentativas", 0) + 1,
        "erro_validacao": "; ".join(problemas),
        "caminho": ["validar"],
    }


def rotear_validacao(estado: Estado) -> str:
    if estado.get("erro_validacao") and estado.get("tentativas", 0) < MAX_TENTATIVAS:
        return "redigir"
    if precisa_chefia(estado["pedido"], estado.get("violacoes", [])):
        return "chefia"
    return "registrar"


# ------------------------------------------------------------------ ETAPA 8: a chefia
def aprovacao_chefia(estado: Estado) -> dict:
    resposta = interrupt({
        "pergunta": "Aprova o despacho?",
        "chefia": estado["servidor"].get("chefia"),
        "resumo": f"Servidor: {estado['servidor'].get('nome')} ({estado['pedido'].get('matricula')})\n"
                  f"Período: {estado['pedido'].get('data_inicio')} a {estado['pedido'].get('data_fim')}\n"
                  f"Despacho: {estado.get('despacho')}",
    })
    aprovado = bool(resposta.get("aprovado")) if isinstance(resposta, dict) else (resposta == "sim")
    aprovador = resposta.get("aprovador", "") if isinstance(resposta, dict) else ""
    motivo = resposta.get("motivo", "") if isinstance(resposta, dict) else ""
    if aprovado:
        return {
            "aprovador": aprovador,
            "feedback_chefia": "",
            "caminho": ["aprovacao_chefia"],
        }
    else:
        return {
            "rejeicoes": estado.get("rejeicoes", 0) + 1,
            "feedback_chefia": motivo,
            "aprovador": aprovador,
            "caminho": ["aprovacao_chefia"],
        }


def rotear_chefia(estado: Estado) -> str:
    if estado.get("feedback_chefia"):
        if estado.get("rejeicoes", 0) < MAX_REJEICOES:
            return "redigir"
        return "registrar"
    return "registrar"


# ------------------------------------------------------------------ ETAPA 7.5 / 9.4: registrar
def registrar(estado: Estado) -> dict:
    despacho = estado.get("despacho", "")
    if encontrar_dado_sensivel(despacho):
        return {"resultado": "bloqueado_na_saida", "caminho": ["registrar"]}

    p = estado["pedido"]
    protocolo = estado.get("protocolo") or gerar_protocolo(
        p.get("matricula"),
        p.get("tipo"),
        p.get("data_inicio"),
        p.get("data_fim"),
    )

    if estado.get("rejeicoes", 0) >= MAX_REJEICOES:
        decisao = "indeferido_pela_chefia"
    else:
        decisao = estado.get("decisao", "deferido")

    aprovador = estado.get("aprovador") or "sistema"

    res = chamar_mcp("registrar_decisao", {
        "protocolo": protocolo,
        "matricula": p["matricula"],
        "tipo": p["tipo"],
        "inicio": p["data_inicio"],
        "fim": p["data_fim"],
        "decisao": decisao,
        "aprovador": aprovador,
        "despacho": despacho,
        "token": os.getenv("TOKEN_REGISTRO", ""),
        "thread_id": estado.get("thread_id", ""),
    })

    if res.get("sucesso"):
        resultado = "duplicado" if res.get("duplicado") else "registrado"
    else:
        resultado = f"negado_pelo_server: {res.get('erro', 'erro')}"

    return {
        "protocolo": protocolo,
        "resultado": resultado,
        "caminho": ["registrar"],
    }


# ------------------------------------------------------------------ ETAPA 7.5: o grafo
def construir(checkpointer=None):
    construtor = StateGraph(Estado)
    nos = {
        "receber": receber, "proteger_entrada": proteger_entrada,
        "recusar": recusar, "extrair": no_extrair, "pedir_dados": pedir_dados,
        "identificar_servidor": identificar_servidor, "calcular_periodo": calcular_periodo,
        "normas": no_normas, "escala": no_escala, "financeiro": no_financeiro, "consolidar": consolidar,
        "redigir_despacho": redigir_despacho, "validar": validar, "aprovacao_chefia": aprovacao_chefia,
        "registrar": registrar,
    }
    if USAR_TRIAGEM_HUMANA:
        nos["triagem_humana"] = triagem_humana
    for nome, funcao in nos.items():
        construtor.add_node(nome, medido(nome, funcao))

    construtor.add_edge(START, "receber")
    construtor.add_edge("receber", "proteger_entrada")

    if USAR_TRIAGEM_HUMANA:
        construtor.add_conditional_edges(
            "proteger_entrada",
            rotear_entrada,
            {"bloqueado": "recusar", "ok": "extrair", "duvida": "triagem_humana"},
        )
        construtor.add_conditional_edges(
            "triagem_humana",
            rotear_triagem,
            {"segue": "extrair", "recusa": "recusar"},
        )
    else:
        construtor.add_conditional_edges(
            "proteger_entrada",
            rotear_entrada,
            {"bloqueado": "recusar", "ok": "extrair"},
        )

    construtor.add_conditional_edges(
        "extrair",
        rotear_extracao,
        {"ok": "identificar_servidor", "incompleto": "pedir_dados"},
    )
    construtor.add_conditional_edges(
        "identificar_servidor",
        rotear_servidor,
        {"ok": "calcular_periodo", "nao_encontrado": "pedir_dados"},
    )

    # fan-out
    construtor.add_edge("calcular_periodo", "normas")
    construtor.add_edge("calcular_periodo", "escala")
    construtor.add_edge("calcular_periodo", "financeiro")

    # fan-in
    construtor.add_edge(["normas", "escala", "financeiro"], "consolidar")
    construtor.add_edge("consolidar", "redigir_despacho")
    construtor.add_edge("redigir_despacho", "validar")

    construtor.add_conditional_edges(
        "validar",
        rotear_validacao,
        {"redigir": "redigir_despacho", "chefia": "aprovacao_chefia", "registrar": "registrar"},
    )

    construtor.add_conditional_edges(
        "aprovacao_chefia",
        rotear_chefia,
        {"redigir": "redigir_despacho", "registrar": "registrar"},
    )

    construtor.add_edge("pedir_dados", END)
    construtor.add_edge("recusar", END)
    construtor.add_edge("registrar", END)

    return construtor.compile(checkpointer=checkpointer)


# ------------------------------------------------------------------ execução
def config_de(thread_id: str) -> dict:
    """(PRONTO) thread_id identifica a execução no checkpointer; recursion_limit protege contra ciclo infinito."""
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": 40}


def mostrar(app, thread_id: str, saida: dict) -> None:
    """(PRONTO) Imprime o caminho, o desfecho e, se pausou, a pergunta feita ao humano."""
    print(f"\n[{thread_id}] caminho: {' -> '.join(saida.get('caminho', []))}")
    if "__interrupt__" in saida:
        pausa = saida["__interrupt__"][0].value
        print(f"[{thread_id}] PAUSADO em {app.get_state(config_de(thread_id)).next}: {pausa.get('pergunta')}")
        print(f"   retome com: python etapa07_grafo.py --retomar {thread_id} sim|\"nao:motivo\" <quem>")
    else:
        print(f"[{thread_id}] resultado: {saida.get('resultado')}  protocolo: {saida.get('protocolo')}")
        print(f"[{thread_id}] despacho: {saida.get('despacho')}")


def executar(app, thread_id: str, texto: str) -> None:
    """(PRONTO) Roda um pedido do começo. Apaga antes os checkpoints desse thread_id: sem isso, rodar o mesmo caso
    de novo CONTINUARIA a execução anterior, e os campos com reducer (caminho, violacoes) acumulariam as duas."""
    if app.checkpointer:
        app.checkpointer.delete_thread(thread_id)
    saida = app.invoke({"thread_id": thread_id, "texto": texto}, config_de(thread_id))
    mostrar(app, thread_id, saida)


def listar_pendentes(app, checkpointer) -> None:
    """(PRONTO) Cada execução (thread_id) que está no checkpointer e onde ela parou. Quem tem `next` não vazio está
    PAUSADO esperando um humano. É o get_state() da aula7/exemplos/04_persistencia/main.py, para todas as execuções."""
    vistos = []
    for checkpoint in checkpointer.list(None):  # todos os checkpoints de todas as execuções
        thread_id = checkpoint.config["configurable"]["thread_id"]
        if thread_id not in vistos:
            vistos.append(thread_id)
    for thread_id in vistos:
        proximo = app.get_state(config_de(thread_id)).next
        print(f"{thread_id}: {'PAUSADO em ' + str(proximo) if proximo else 'concluído'}")


def retomar(app, thread_id: str, resposta: str, quem: str) -> None:
    proximo = app.get_state(config_de(thread_id)).next
    if not proximo:
        print(f"[{thread_id}] nada pausado")
        return
    if proximo == ("aprovacao_chefia",):
        aprovado = (resposta.strip().lower() == "sim")
        motivo = ""
        if not aprovado and ":" in resposta:
            motivo = resposta.split(":", 1)[1].strip()
        elif not aprovado:
            motivo = resposta
        valor = {"aprovado": aprovado, "aprovador": quem, "motivo": motivo}
    elif proximo == ("triagem_humana",):
        segue = resposta.strip().lower().startswith("sim")
        orientacao = ""
        if ":" in resposta:
            orientacao = resposta.split(":", 1)[1].strip()
        valor = {"segue": segue, "orientacao": orientacao, "quem": quem}
    else:
        valor = resposta
    saida = app.invoke(Command(resume=valor), config_de(thread_id))
    mostrar(app, thread_id, saida)


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--sem-guardrail"]
    # ETAPA 8.1 -- o checkpointer SQLite: é ele que deixa a execução pausada sobreviver ao fim do processo.
    with SqliteSaver.from_conn_string(str(SAIDAS / "checkpoints.db")) as checkpointer:
        app = construir(checkpointer)
        if not args or args[0] == "--mermaid":
            print(app.get_graph().draw_mermaid())
        elif args[0] == "--pendentes":
            listar_pendentes(app, checkpointer)
        elif args[0] == "--retomar":
            retomar(app, args[1], args[2], args[3] if len(args) > 3 else "?")
        elif args[0] == "--todos":
            for nome, caso in CASOS.items():
                executar(app, nome, caso["pedido"])
        else:
            executar(app, args[0], CASOS[args[0]]["pedido"])


if __name__ == "__main__":
    print(f"[modelo] {MODELO}  [guardrail de entrada {'ligado' if GUARDRAIL_LIGADO else 'DESLIGADO'}]")
    main()
