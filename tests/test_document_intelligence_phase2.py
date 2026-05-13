from pathlib import Path

import pytest

from canonical_tools import tool_schema_by_name
from document_intelligence_agent import DocumentIntelligenceAgent
from planner_agent import PlannerAgent
from tool_document_intelligence import DocumentIntelligenceTool


@pytest.mark.asyncio
async def test_document_index_searches_with_metadata_filters(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    contract = docs / "contrato_servicos.txt"
    invoice = docs / "nota_fiscal_001.txt"
    contract.write_text("Contrato de prestacao de servicos com clausula de pagamento.", encoding="utf-8")
    invoice.write_text("Nota fiscal NF-e com CNPJ e valor total de 1500 reais.", encoding="utf-8")

    agent = DocumentIntelligenceAgent(index_path=tmp_path / "document_index.json")

    indexed = await agent.index_documents(str(docs), limit=10)
    assert indexed["indexed"] == 2

    contract_result = await agent.search_index("clausula pagamento", filters={"kind": "contract"})
    assert len(contract_result["matches"]) == 1
    assert contract_result["matches"][0]["path"].endswith("contrato_servicos.txt")

    invoice_result = await agent.search_index("valor total", filters={"extension": ".txt", "kind": "invoice"})
    assert len(invoice_result["matches"]) == 1
    assert invoice_result["matches"][0]["classification"]["kind"] == "invoice"


@pytest.mark.asyncio
async def test_discover_documents_finds_email_attachments(tmp_path):
    message = tmp_path / "pedido.eml"
    message.write_text(
        "\r\n".join(
            [
                "From: financeiro@example.com",
                "To: ops@example.com",
                "Subject: Nota fiscal com anexo",
                "MIME-Version: 1.0",
                'Content-Type: multipart/mixed; boundary="x"',
                "",
                "--x",
                "Content-Type: text/plain; charset=utf-8",
                "",
                "Segue nota fiscal e boleto em anexo.",
                "--x",
                "Content-Type: application/pdf",
                'Content-Disposition: attachment; filename="nota-fiscal.pdf"',
                "",
                "fake",
                "--x--",
            ]
        ),
        encoding="utf-8",
    )

    agent = DocumentIntelligenceAgent(index_path=tmp_path / "document_index.json")
    result = await agent.discover_documents(str(tmp_path), filters={"kind": "email"})

    assert result["matches"]
    assert result["matches"][0]["classification"]["kind"] == "email"
    assert result["matches"][0]["metadata"]["attachments"][0]["filename"] == "nota-fiscal.pdf"


@pytest.mark.asyncio
async def test_document_intelligence_tool_exposes_index_actions(tmp_path):
    doc = tmp_path / "invoice.txt"
    doc.write_text("Invoice number 123 with total amount due.", encoding="utf-8")
    tool = DocumentIntelligenceTool()
    tool.agent = DocumentIntelligenceAgent(index_path=tmp_path / "document_index.json")

    index_result = await tool.run({"action": "index_documents", "root": str(tmp_path)})
    assert index_result.status == "succeeded"
    assert index_result.data["indexed"] == 1

    search_result = await tool.run({"action": "search_index", "query": "total amount", "filters": {"kind": "invoice"}})
    assert search_result.status == "succeeded"
    assert len(search_result.data["matches"]) == 1


@pytest.mark.asyncio
async def test_planner_and_schema_include_phase2_document_actions(tmp_path):
    schema = tool_schema_by_name("document_intelligence")
    action_enum = schema["function"]["parameters"]["properties"]["action"]["enum"]
    assert {"index_documents", "search_index", "discover_documents"}.issubset(set(action_enum))

    docs = tmp_path / "docs"
    docs.mkdir()
    plan, validation = await PlannerAgent().parse(f"index documents {docs}")

    assert validation.ok
    assert plan.tasks[0].tool == "document_intelligence"
    assert plan.tasks[0].action == "index_documents"
    assert plan.tasks[0].params["root"].lower() == str(docs).lower()
