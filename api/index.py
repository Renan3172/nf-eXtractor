import re
import base64
import requests
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from google import genai
from google.genai import types

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/api/processar-nota")
async def processar_nota(
    imagem: UploadFile = File(...),
    gemini_api_key: str = Form(...),
    meudanfe_api_key: str = Form(...)
):
    conteudo_imagem = await imagem.read()
    mime_type = imagem.content_type or "image/jpeg"

    try:
        client = genai.Client(api_key=gemini_api_key.strip())
        prompt = (
            "Analise esta imagem de uma DANFE ou documento fiscal. "
            "Extraia EXCLUSIVAMENTE os 44 dígitos numéricos da Chave de Acesso da NF-e. "
            "Retorne apenas os 44 números seguidos, sem espaços, pontos, traços ou qualquer texto adicional."
        )
        response = client.models.generate_content(
            model="gemini-3.8-flash",
            contents=[
                types.Part.from_bytes(data=conteudo_imagem, mime_type=mime_type),
                prompt,
            ],
        )
        chave_bruta = response.text.strip()
        chave_acesso = re.sub(r"\D", "", chave_bruta)

        if len(chave_acesso) != 44:
            raise HTTPException(
                status_code=422,
                detail=f"Não foi possível identificar uma chave válida de 44 dígitos. Leitura obtida: '{chave_bruta}'"
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erro na API do Gemini: {str(e)}")

    try:
        chave_api = meudanfe_api_key.strip()
        headers = {
            "Api-Key": chave_api,
            "Accept": "application/json, application/xml, text/plain"
        }

        # Consulta XML na rota pública/gratuita v1 do MeuDanfe
        url_xml = f"https://api.meudanfe.com.br/v1/fd/get/xml/{chave_acesso}?api_key={chave_api}"
        res_xml = requests.get(url_xml, headers=headers, timeout=30)

        if res_xml.status_code != 200:
            raise HTTPException(
                status_code=res_xml.status_code,
                detail=f"Erro no MeuDanfe ({res_xml.status_code}): {res_xml.text}"
            )

        # Trata retorno em JSON ou XML puro
        xml_base64 = None
        content_type_xml = res_xml.headers.get("Content-Type", "")
        if "application/json" in content_type_xml:
            dados_xml = res_xml.json()
            xml_base64 = dados_xml.get("data") or dados_xml.get("xml")
        else:
            xml_base64 = base64.b64encode(res_xml.content).decode("utf-8")

        # Consulta PDF (DANFE) na rota v1
        url_pdf = f"https://api.meudanfe.com.br/v1/fd/get/da/{chave_acesso}?api_key={chave_api}"
        res_pdf = requests.get(url_pdf, headers=headers, timeout=30)
        pdf_base64 = None
        if res_pdf.status_code == 200:
            content_type_pdf = res_pdf.headers.get("Content-Type", "")
            if "application/json" in content_type_pdf:
                dados_pdf = res_pdf.json()
                pdf_base64 = dados_pdf.get("data") or dados_pdf.get("pdf")
            else:
                pdf_base64 = base64.b64encode(res_pdf.content).decode("utf-8")

        return {
            "status": "sucesso",
            "chave_acesso": chave_acesso,
            "xml_base64": xml_base64,
            "pdf_base64": pdf_base64
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao comunicar com MeuDanfe: {str(e)}")
