"""Pruebas UNITARIAS del módulo de IA (app/ia.py).

Nivel unitario a propósito: no levantan Postgres, Redis ni el worker, y no
salen a internet. La llamada a OpenRouter se sustituye con `unittest.mock`,
porque lo que es responsabilidad de Atlas es el CONTRATO con el modelo (qué se
le pide, cómo se lee su respuesta, qué pasa si falla), no la redacción que el
modelo decida producir, que cambia en cada llamada.

Ejecutar desde la raíz del backend:
    python -m unittest discover -s tests -v
"""
import io
import json
import unittest
import urllib.error
from unittest import mock

from app import ia


def _respuesta_openrouter(texto: str, modelo: str = "anthropic/claude-haiku-4.5"):
    cuerpo = json.dumps(
        {"model": modelo, "choices": [{"message": {"role": "assistant", "content": texto}}]}
    ).encode("utf-8")
    respuesta = mock.MagicMock()
    respuesta.read.return_value = cuerpo
    respuesta.__enter__.return_value = respuesta
    return respuesta


def _generar(**cambios):
    parametros = dict(
        titulo="Índices en Postgres",
        contenido="Un índice bien puesto bajó la consulta de 800 ms a 12 ms.",
        tono="profesional",
        red_social="linkedin",
        api_key="sk-or-prueba",
        modelo="anthropic/claude-haiku-4.5",
        timeout_s=5,
        latencia_simulada_ms=0,
    )
    parametros.update(cambios)
    return ia.generar_publicacion(**parametros)


class PromptTest(unittest.TestCase):
    def test_el_prompt_lleva_el_tono_la_red_y_la_idea(self):
        sistema, usuario = ia.construir_mensajes("Título", "Mi idea", "cercano", "x")
        self.assertEqual(sistema["role"], "system")
        self.assertIn(ia.TONOS["cercano"], sistema["content"])
        self.assertIn(ia.REDES["x"], sistema["content"])
        self.assertIn("Mi idea", usuario["content"])


class AjusteTest(unittest.TestCase):
    def test_un_post_de_x_nunca_supera_280_caracteres(self):
        texto = ia.ajustar_a_red("a" * 400, "x")
        self.assertEqual(len(texto), ia.LIMITE_X)

    def test_quita_las_comillas_que_envuelven_la_respuesta(self):
        self.assertEqual(ia.ajustar_a_red('"Hola mundo"', "instagram"), "Hola mundo")


class GeneracionTest(unittest.TestCase):
    def test_sin_api_key_usa_el_generador_simulado_sin_tocar_la_red(self):
        with mock.patch("urllib.request.urlopen") as urlopen:
            generado = _generar(api_key=None)
        urlopen.assert_not_called()
        self.assertEqual(generado.modelo, ia.MODELO_SIMULADO)

    def test_con_api_key_devuelve_el_texto_y_el_modelo_real(self):
        with mock.patch("urllib.request.urlopen", return_value=_respuesta_openrouter("  Post listo  ")) as urlopen:
            generado = _generar()
        self.assertEqual(generado.texto, "Post listo")
        self.assertEqual(generado.modelo, "anthropic/claude-haiku-4.5")
        peticion = urlopen.call_args.args[0]
        self.assertEqual(peticion.get_header("Authorization"), "Bearer sk-or-prueba")

    def test_una_respuesta_vacia_es_un_error_no_una_publicacion_vacia(self):
        with mock.patch("urllib.request.urlopen", return_value=_respuesta_openrouter("   ")):
            with self.assertRaises(ia.ErrorIA):
                _generar()

    def test_sin_saldo_se_explica_al_usuario(self):
        error = urllib.error.HTTPError(
            ia.OPENROUTER_URL, 402, "Payment Required", {},
            io.BytesIO(b'{"error": {"message": "Insufficient credits"}}'),
        )
        with mock.patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(ia.ErrorIA) as capturado:
                _generar()
        self.assertIn("saldo", str(capturado.exception))


if __name__ == "__main__":
    unittest.main()
