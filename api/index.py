import re
import time
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
    meudanfe_api_key: str = Form("")
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

        # Lista de modelos em ordem de prioridade (evita falha por pico de demanda 503)
        modelos_tentativa = ["gemini-3.8-flash", "gemini-2.5-flash-lite", "gemini-3.0-flash"]
        response = None
        ultimo_erro = None

        for modelo in modelos_tentativa:
            try:
                response = client.models.generate_content(
                    model=modelo,
                    contents=[
                        types.Part.from_bytes(data=conteudo_imagem, mime_type=mime_type),
                        prompt,
                    ],
                )
                if response and response.text:
                    break
            except Exception as err:
                ultimo_erro = err
                time.sleep(1)

        if not response or not response.text:
            raise ultimo_erro or Exception("Nenhum modelo respondeu no momento.")

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
        # Consulta gratuita na API pública ConsultaDanfe
        url_consulta = "https://consultadanfe.com/api/v1/consulta"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "ExtratorNFe/1.0"
        }
        payload = {"chave": chave_acesso}
        res = requests.post(url_consulta, json=payload, headers=headers, timeout=30)

        if res.status_code != 200:
            raise HTTPException(
                status_code=res.status_code,
                detail=f"Erro na consulta pública da NF-e ({res.status_code}): {res.text}"
            )

        dados = res.json()
        xml_texto = dados.get("xml") or dados.get("data", {}).get("xml")
        pdf_base64 = dados.get("pdf") or dados.get("danfe") or dados.get("data", {}).get("pdf")

        if not xml_texto:
            raise HTTPException(
                status_code=404,
                detail="A consulta retornou resposta, mas o XML da nota não estava disponível (verifique se a nota é recente)."
            )

        if xml_texto.strip().startswith("<"):
            xml_base64 = base64.b64encode(xml_texto.encode("utf-8")).decode("utf-8")
        else:
            xml_base64 = xml_texto

        return {
            "status": "sucesso",
            "chave_acesso": chave_acesso,
            "xml_base64": xml_base64,
            "pdf_base64": pdf_base64
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao consultar NF-e: {str(e)}")
