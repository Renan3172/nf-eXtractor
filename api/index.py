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
    meudanfe_api_key: str = Form("")
):
    conteudo_imagem = await imagem.read()
    mime_type = imagem.content_type or "image/jpeg"

    try:
        client = genai.Client(api_key=gemini_api_key.strip())
        prompt = (
            "Extraia EXCLUSIVAMENTE os 44 dígitos numéricos da Chave de Acesso da NF-e nesta imagem. "
            "Retorne apenas os 44 números seguidos, sem espaços ou texto adicional."
        )
        config_rapida = types.GenerateContentConfig(
            temperature=0.0,
            max_output_tokens=64,
        )

        # Prioriza o modelo mais rápido para evitar timeout na Vercel
        modelos_tentativa = ["gemini-3.5-flash-lite", "gemini-3.8-flash"]
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
                    config=config_rapida,
                )
                if response and response.text:
                    break
            except Exception as err:
                ultimo_erro = err

        if not response or not response.text:
            raise ultimo_erro or Exception("Nenhum modelo respondeu a tempo.")

        chave_bruta = response.text.strip()
        chave_acesso = re.sub(r"\D", "", chave_bruta)

        if len(chave_acesso) != 44:
            raise HTTPException(
                status_code=422,
                detail=f"Chave inválida (44 dígitos esperados). Leitura obtida: '{chave_bruta}'"
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erro na API do Gemini: {str(e)}")

    try:
        url_consulta = "https://consultadanfe.com/api/v1/consulta"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, application/pdf",
            "User-Agent": "ExtratorNFe/1.0"
        }
        res = requests.post(
            url_consulta,
            json={"chave": chave_acesso},
            headers=headers,
            timeout=8
        )

        if res.status_code != 200:
            raise HTTPException(
                status_code=res.status_code,
                detail=f"Erro na consulta pública da NF-e ({res.status_code}): {res.text}"
            )

        content_type = res.headers.get("Content-Type", "").lower()
        xml_base64 = None
        pdf_base64 = None

        if "application/pdf" in content_type:
            pdf_base64 = base64.b64encode(res.content).decode("utf-8")
        else:
            dados = res.json()
            bloco = dados.get("data") if isinstance(dados.get("data"), dict) else {}

            xml_texto = (
                dados.get("xml")
                or dados.get("xml_base64")
                or bloco.get("xml")
                or bloco.get("xml_base64")
            )
            pdf_base64 = (
                dados.get("pdf")
                or dados.get("pdf_base64")
                or dados.get("danfe")
                or dados.get("danfe_base64")
                or bloco.get("pdf")
                or bloco.get("pdf_base64")
                or bloco.get("danfe")
            )

            if xml_texto:
                if xml_texto.strip().startswith("<"):
                    xml_base64 = base64.b64encode(xml_texto.encode("utf-8")).decode("utf-8")
                else:
                    xml_base64 = xml_texto

            if not xml_base64 and not pdf_base64:
                raise HTTPException(
                    status_code=422,
                    detail=f"A API respondeu sem XML/PDF nos campos esperados. Campos: {list(dados.keys())} | Amostra: {str(dados)[:300]}"
                )

        return {
            "status": "sucesso",
            "chave_acesso": chave_acesso,
            "xml_base64": xml_base64,
            "pdf_base64": pdf_base64
        }

    except requests.Timeout:
        raise HTTPException(
            status_code=504,
            detail="O servidor do ConsultaDanfe demorou mais de 8 segundos para responder. Tente enviar novamente."
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao consultar NF-e: {str(e)}")
