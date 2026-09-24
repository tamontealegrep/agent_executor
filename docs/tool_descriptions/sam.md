# Tool descriptions — Sam

Generado desde `src\agents\family_aims_sam\agent.json` (version `1.0.0`). Este es el texto y schema exactos que `ChatOpenAI.bind_tools()` recibe hoy (canonico LangChain -> convertido a OpenAI).

## Indice

- [end_call](#end-call)
- [callback](#callback)
- [time_now](#time-now)
- [check_visa](#check-visa)
- [get_available_slots](#get-available-slots)
- [book_appointment](#book-appointment)
- [find_appointment](#find-appointment)
- [cancel_appointment](#cancel-appointment)
- [edit_appointment](#edit-appointment)

## end_call

**Descripcion final (lo que el modelo lee en `function.description`):**

```text
Cierra la conversacion cuando el flujo llega a un estado terminal que lo requiere explicitamente.
Ejecuta end_call unicamente cuando el nodo activo del flujo indica EXECUTE: end_call, por ejemplo al cerrar el callback despues de registrarlo con exito.
```

_Sin parametros._

<details><summary>JSON schema OpenAI (formato real enviado a bind_tools)</summary>

```json
{
  "type": "function",
  "function": {
    "name": "end_call",
    "description": "Cierra la conversacion cuando el flujo llega a un estado terminal que lo requiere explicitamente.\nEjecuta end_call unicamente cuando el nodo activo del flujo indica EXECUTE: end_call, por ejemplo al cerrar el callback despues de registrarlo con exito.",
    "parameters": {
      "type": "object",
      "properties": {},
      "required": [],
      "additionalProperties": false
    }
  }
}
```

</details>

## callback

**Descripcion final (lo que el modelo lee en `function.description`):**

```text
Normaliza una solicitud de callback cuando el usuario quiere seguimiento humano o la agenda no puede resolverse en el chat.
Ejecuta callback cuando el usuario pida seguimiento humano, cuando la agenda no se pueda resolver en chat o cuando un error tecnico impida completar la reserva.
contact_name: Nombre completo del contacto.
contact_phone: Telefono del contacto. Requerido si no se provee contact_email.
contact_email: Correo del contacto. Requerido si no se provee contact_phone.
reason: Motivo del callback.
context: Contexto adicional para el asesor humano.
iana_timezone: Zona horaria IANA del contacto.
preferred_days: Dias preferidos para el callback.
preferred_time_window: Franja horaria preferida para el callback.
```

**Parametros:**

| Nombre | Tipo | Requerido | Enum | Ejemplo | Descripcion |
|---|---|---|---|---|---|
| `contact_name` | string | si | - | Maria Gomez | Nombre completo del contacto. |
| `contact_phone` | string | no | - | +573001234567 | Telefono del contacto. Requerido si no se provee contact_email. |
| `contact_email` | string | no | - | maria@example.com | Correo del contacto. Requerido si no se provee contact_phone. |
| `reason` | string | si | - | booking_failed | Motivo del callback. |
| `context` | string | no | - | - | Contexto adicional para el asesor humano. |
| `iana_timezone` | string | si | - | America/Bogota | Zona horaria IANA del contacto. |
| `preferred_days` | string | no | - | - | Dias preferidos para el callback. |
| `preferred_time_window` | string | no | morning, midday, afternoon, evening, any | afternoon | Franja horaria preferida para el callback. |

<details><summary>JSON schema OpenAI (formato real enviado a bind_tools)</summary>

```json
{
  "type": "function",
  "function": {
    "name": "callback",
    "description": "Normaliza una solicitud de callback cuando el usuario quiere seguimiento humano o la agenda no puede resolverse en el chat.\nEjecuta callback cuando el usuario pida seguimiento humano, cuando la agenda no se pueda resolver en chat o cuando un error tecnico impida completar la reserva.\ncontact_name: Nombre completo del contacto.\ncontact_phone: Telefono del contacto. Requerido si no se provee contact_email.\ncontact_email: Correo del contacto. Requerido si no se provee contact_phone.\nreason: Motivo del callback.\ncontext: Contexto adicional para el asesor humano.\niana_timezone: Zona horaria IANA del contacto.\npreferred_days: Dias preferidos para el callback.\npreferred_time_window: Franja horaria preferida para el callback.",
    "parameters": {
      "type": "object",
      "properties": {
        "contact_name": {
          "type": "string",
          "description": "Nombre completo del contacto.",
          "examples": [
            "Maria Gomez"
          ]
        },
        "contact_phone": {
          "type": "string",
          "description": "Telefono del contacto. Requerido si no se provee contact_email.",
          "examples": [
            "+573001234567"
          ]
        },
        "contact_email": {
          "type": "string",
          "description": "Correo del contacto. Requerido si no se provee contact_phone.",
          "examples": [
            "maria@example.com"
          ]
        },
        "reason": {
          "type": "string",
          "description": "Motivo del callback.",
          "examples": [
            "booking_failed"
          ]
        },
        "context": {
          "type": "string",
          "description": "Contexto adicional para el asesor humano."
        },
        "iana_timezone": {
          "type": "string",
          "description": "Zona horaria IANA del contacto.",
          "examples": [
            "America/Bogota"
          ]
        },
        "preferred_days": {
          "type": "string",
          "description": "Dias preferidos para el callback."
        },
        "preferred_time_window": {
          "type": "string",
          "description": "Franja horaria preferida para el callback.",
          "enum": [
            "morning",
            "midday",
            "afternoon",
            "evening",
            "any"
          ],
          "examples": [
            "afternoon"
          ]
        }
      },
      "required": [
        "contact_name",
        "reason",
        "iana_timezone"
      ],
      "additionalProperties": false
    }
  }
}
```

</details>

## time_now

**Descripcion final (lo que el modelo lee en `function.description`):**

```text
Obtiene la fecha y hora actual en una zona horaria IANA para normalizar fechas relativas y validar ventanas de agenda.
Call time_now whenever the current local date or time is needed to process time-sensitive context or validate upcoming dates.
TIMEZONE INFERENCE RULE: Infer the contact's IANA timezone from any location, country, or city mentioned in the conversation (e.g. 'Indonesia' → 'Asia/Jakarta', 'Mexico' → 'America/Mexico_City', 'Spain' or 'Madrid' → 'Europe/Madrid', 'Georgia' → 'Asia/Tbilisi'). Use the most specific clue available. If no location is mentioned, default to 'America/Bogota'.
ARGUMENT RULE: Always pass the inferred IANA timezone string as the 'timezone' parameter. Never leave it empty if a location clue exists in the conversation.
timezone: Zona horaria IANA inferida del contexto de la conversación (país, ciudad o región mencionados por el contacto).
```

**Parametros:**

| Nombre | Tipo | Requerido | Enum | Ejemplo | Descripcion |
|---|---|---|---|---|---|
| `timezone` | string | no | - | America/Bogota | Zona horaria IANA inferida del contexto de la conversación (país, ciudad o región mencionados por el contacto). |

<details><summary>JSON schema OpenAI (formato real enviado a bind_tools)</summary>

```json
{
  "type": "function",
  "function": {
    "name": "time_now",
    "description": "Obtiene la fecha y hora actual en una zona horaria IANA para normalizar fechas relativas y validar ventanas de agenda.\nCall time_now whenever the current local date or time is needed to process time-sensitive context or validate upcoming dates.\nTIMEZONE INFERENCE RULE: Infer the contact's IANA timezone from any location, country, or city mentioned in the conversation (e.g. 'Indonesia' → 'Asia/Jakarta', 'Mexico' → 'America/Mexico_City', 'Spain' or 'Madrid' → 'Europe/Madrid', 'Georgia' → 'Asia/Tbilisi'). Use the most specific clue available. If no location is mentioned, default to 'America/Bogota'.\nARGUMENT RULE: Always pass the inferred IANA timezone string as the 'timezone' parameter. Never leave it empty if a location clue exists in the conversation.\ntimezone: Zona horaria IANA inferida del contexto de la conversación (país, ciudad o región mencionados por el contacto).",
    "parameters": {
      "type": "object",
      "properties": {
        "timezone": {
          "type": "string",
          "description": "Zona horaria IANA inferida del contexto de la conversación (país, ciudad o región mencionados por el contacto).",
          "examples": [
            "America/Bogota"
          ]
        }
      },
      "required": [],
      "additionalProperties": false
    }
  }
}
```

</details>

## check_visa

**Descripcion final (lo que el modelo lee en `function.description`):**

```text
Revisa si una o varias nacionalidades requieren visa para ingresar a Colombia como turista.
Ejecuta esta accion cuando el contacto mencione su nacionalidad o pregunte si necesita visa para viajar a Colombia como turista.
MANDATORY EXECUTION TRIGGER: ejecuta esta accion cuando el contacto mencione su nacionalidad o pregunte si necesita visa para viajar a Colombia.
ARGUMENT RULE: nationalities puede ser un string separado por comas ('USA, CHN') o una lista de strings (['USA', 'CHN']). Envia los nombres de paises como codigos ISO 3166-1 alfa-3. No envies gentilicios como 'mexicana' o 'venezolana'.
SOURCE OF TRUTH RULE: la respuesta agregada de esta herramienta vive en los campos nationality, requires_visa, conditional_visa y summary. Revisa conditional_visa para casos como China o India donde se requiere visa salvo que exista visa USA/Schengen.
ERROR RULE: si success es false, esta implementacion eleva el problema como error HTTP y no debes inferir un resultado parcial.
nationalities: Uno o más países o códigos ISO 3166-1 alfa-3 a revisar. Ejemplos: 'Mexico', 'China, India', ['Spain', 'AFG'], 'MEX'. No usar gentilicios como 'mexicana'.
input nationalities (required): Uno o más países o códigos ISO 3166-1 alfa-3 a revisar. Ejemplos: 'Mexico', 'China, India', ['Spain', 'AFG'], 'MEX'. No usar gentilicios como 'mexicana'.
output success (required): Valor booleano que indica si la consulta se ejecuto correctamente.
output nationality (required): Nombre de la(s) nacionalidad(es) revisada(s), limpio de espacios.
output requires_visa (required): Indica si la persona requiere visa para entrar a Colombia.
output conditional_visa (required): Indica si el requisito esta condicionado a visa USA/Schengen.
output summary (required): Resumen legible de los requisitos encontrados.
output errors (required): Mensaje de error tecnico si la consulta fallo. null en caso de exito.
```

**Parametros:**

| Nombre | Tipo | Requerido | Enum | Ejemplo | Descripcion |
|---|---|---|---|---|---|
| `nationalities` | string | si | - | MEX, CHN | Uno o más países o códigos ISO 3166-1 alfa-3 a revisar. Ejemplos: 'Mexico', 'China, India', ['Spain', 'AFG'], 'MEX'. No usar gentilicios como 'mexicana'. |

<details><summary>JSON schema OpenAI (formato real enviado a bind_tools)</summary>

```json
{
  "type": "function",
  "function": {
    "name": "check_visa",
    "description": "Revisa si una o varias nacionalidades requieren visa para ingresar a Colombia como turista.\nEjecuta esta accion cuando el contacto mencione su nacionalidad o pregunte si necesita visa para viajar a Colombia como turista.\nMANDATORY EXECUTION TRIGGER: ejecuta esta accion cuando el contacto mencione su nacionalidad o pregunte si necesita visa para viajar a Colombia.\nARGUMENT RULE: nationalities puede ser un string separado por comas ('USA, CHN') o una lista de strings (['USA', 'CHN']). Envia los nombres de paises como codigos ISO 3166-1 alfa-3. No envies gentilicios como 'mexicana' o 'venezolana'.\nSOURCE OF TRUTH RULE: la respuesta agregada de esta herramienta vive en los campos nationality, requires_visa, conditional_visa y summary. Revisa conditional_visa para casos como China o India donde se requiere visa salvo que exista visa USA/Schengen.\nERROR RULE: si success es false, esta implementacion eleva el problema como error HTTP y no debes inferir un resultado parcial.\nnationalities: Uno o más países o códigos ISO 3166-1 alfa-3 a revisar. Ejemplos: 'Mexico', 'China, India', ['Spain', 'AFG'], 'MEX'. No usar gentilicios como 'mexicana'.\ninput nationalities (required): Uno o más países o códigos ISO 3166-1 alfa-3 a revisar. Ejemplos: 'Mexico', 'China, India', ['Spain', 'AFG'], 'MEX'. No usar gentilicios como 'mexicana'.\noutput success (required): Valor booleano que indica si la consulta se ejecuto correctamente.\noutput nationality (required): Nombre de la(s) nacionalidad(es) revisada(s), limpio de espacios.\noutput requires_visa (required): Indica si la persona requiere visa para entrar a Colombia.\noutput conditional_visa (required): Indica si el requisito esta condicionado a visa USA/Schengen.\noutput summary (required): Resumen legible de los requisitos encontrados.\noutput errors (required): Mensaje de error tecnico si la consulta fallo. null en caso de exito.",
    "parameters": {
      "type": "object",
      "properties": {
        "nationalities": {
          "type": "string",
          "description": "Uno o más países o códigos ISO 3166-1 alfa-3 a revisar. Ejemplos: 'Mexico', 'China, India', ['Spain', 'AFG'], 'MEX'. No usar gentilicios como 'mexicana'.",
          "examples": [
            "MEX, CHN"
          ]
        }
      },
      "required": [
        "nationalities"
      ],
      "additionalProperties": false
    }
  }
}
```

</details>

## get_available_slots

**Descripcion final (lo que el modelo lee en `function.description`):**

```text
Herramienta autorizada de disponibilidad para las citas comerciales de Family Aims (IVF o Subrogación). Su salida es la única fuente autorizada de días y horas.
Call get_available_slots on any intent to schedule, reschedule, see more options, or retry an appointment. Capture the result before offering any date or time.
MANDATORY EXECUTION TRIGGER: al entrar al nodo SC__SC_AV_I (usa calendar_type: 'IVF') o SC__SC_AV_S (usa calendar_type: 'SUR'), llama get_available_slots como primera acción del turno — imperativo, sin excepción. También ante cualquier intención de agendar, reagendar, ver más opciones o reintentar una cita.
ABSOLUTE EXECUTION RULE: no menciones días, fechas, horas ni disponibilidad, y no enrutes, antes de ejecutar la herramienta y capturar una respuesta válida.
ARGUMENT RULE: iana_timezone es un identificador IANA continente/ciudad del contacto. Si llega vacío, esta implementación usa 'America/Bogota' por defecto. Nunca uses abreviaturas como EST o COT.
ARGUMENT RULE: calendar_type debe ser 'IVF' o 'SUR' según el nodo del flujo en el que se encuentre el contacto. Si envías otro valor, la implementación cae por defecto al calendario IVF, así que el flujo debe evitar ambigüedad y mandar el literal correcto.
SOURCE OF TRUTH RULE: la única fuente de horarios es el array available_slots de la llamada más reciente. Un horario solo es válido si aparece como objeto con start_co, end_co, start_local y end_local. Si errors no es null o available_slots no es una lista válida, trata la respuesta como inválida.
PRESENTATION RULE: al hablar con el contacto usa solo start_local/end_local. start_co/end_co son la hora del calendario en Colombia, solo para uso interno o para copiar luego a book_appointment.
POLICY NOTE: esta herramienta consulta ventanas distintas según calendar_type. IVF y SUR comparten el formato de salida, pero no necesariamente la misma ventana horaria interna.
POST-EXECUTION RULE: si el contacto pide más opciones, avanza dentro del mismo array antes de volver a llamar la herramienta. Al reservar, copia start_co del slot elegido a start_date de book_appointment sin alterar el offset.
calendar_type: Literal que define el calendario a usar: 'IVF' para fertilización in vitro o 'SUR' para gestación subrogada.
iana_timezone: Identificador IANA (continente/ciudad) de la zona horaria actual del contacto. Ejemplos: America/Bogota, Europe/Madrid, America/New_York. Si llega vacío, el endpoint responde usando America/Bogota.
```

**Parametros:**

| Nombre | Tipo | Requerido | Enum | Ejemplo | Descripcion |
|---|---|---|---|---|---|
| `calendar_type` | string | si | IVF, SUR | SUR | Literal que define el calendario a usar: 'IVF' para fertilización in vitro o 'SUR' para gestación subrogada. |
| `iana_timezone` | string | si | - | Asia/Jakarta | Identificador IANA (continente/ciudad) de la zona horaria actual del contacto. Ejemplos: America/Bogota, Europe/Madrid, America/New_York. Si llega vacío, el endpoint responde usando America/Bogota. |

<details><summary>JSON schema OpenAI (formato real enviado a bind_tools)</summary>

```json
{
  "type": "function",
  "function": {
    "name": "get_available_slots",
    "description": "Herramienta autorizada de disponibilidad para las citas comerciales de Family Aims (IVF o Subrogación). Su salida es la única fuente autorizada de días y horas.\nCall get_available_slots on any intent to schedule, reschedule, see more options, or retry an appointment. Capture the result before offering any date or time.\nMANDATORY EXECUTION TRIGGER: al entrar al nodo SC__SC_AV_I (usa calendar_type: 'IVF') o SC__SC_AV_S (usa calendar_type: 'SUR'), llama get_available_slots como primera acción del turno — imperativo, sin excepción. También ante cualquier intención de agendar, reagendar, ver más opciones o reintentar una cita.\nABSOLUTE EXECUTION RULE: no menciones días, fechas, horas ni disponibilidad, y no enrutes, antes de ejecutar la herramienta y capturar una respuesta válida.\nARGUMENT RULE: iana_timezone es un identificador IANA continente/ciudad del contacto. Si llega vacío, esta implementación usa 'America/Bogota' por defecto. Nunca uses abreviaturas como EST o COT.\nARGUMENT RULE: calendar_type debe ser 'IVF' o 'SUR' según el nodo del flujo en el que se encuentre el contacto. Si envías otro valor, la implementación cae por defecto al calendario IVF, así que el flujo debe evitar ambigüedad y mandar el literal correcto.\nSOURCE OF TRUTH RULE: la única fuente de horarios es el array available_slots de la llamada más reciente. Un horario solo es válido si aparece como objeto con start_co, end_co, start_local y end_local. Si errors no es null o available_slots no es una lista válida, trata la respuesta como inválida.\nPRESENTATION RULE: al hablar con el contacto usa solo start_local/end_local. start_co/end_co son la hora del calendario en Colombia, solo para uso interno o para copiar luego a book_appointment.\nPOLICY NOTE: esta herramienta consulta ventanas distintas según calendar_type. IVF y SUR comparten el formato de salida, pero no necesariamente la misma ventana horaria interna.\nPOST-EXECUTION RULE: si el contacto pide más opciones, avanza dentro del mismo array antes de volver a llamar la herramienta. Al reservar, copia start_co del slot elegido a start_date de book_appointment sin alterar el offset.\ncalendar_type: Literal que define el calendario a usar: 'IVF' para fertilización in vitro o 'SUR' para gestación subrogada.\niana_timezone: Identificador IANA (continente/ciudad) de la zona horaria actual del contacto. Ejemplos: America/Bogota, Europe/Madrid, America/New_York. Si llega vacío, el endpoint responde usando America/Bogota.",
    "parameters": {
      "type": "object",
      "properties": {
        "calendar_type": {
          "type": "string",
          "description": "Literal que define el calendario a usar: 'IVF' para fertilización in vitro o 'SUR' para gestación subrogada.",
          "enum": [
            "IVF",
            "SUR"
          ],
          "examples": [
            "SUR"
          ]
        },
        "iana_timezone": {
          "type": "string",
          "description": "Identificador IANA (continente/ciudad) de la zona horaria actual del contacto. Ejemplos: America/Bogota, Europe/Madrid, America/New_York. Si llega vacío, el endpoint responde usando America/Bogota.",
          "examples": [
            "Asia/Jakarta"
          ]
        }
      },
      "required": [
        "calendar_type",
        "iana_timezone"
      ],
      "additionalProperties": false
    }
  }
}
```

</details>

## book_appointment

**Descripcion final (lo que el modelo lee en `function.description`):**

```text
Herramienta autorizada de reserva para las citas comerciales de Family Aims (IVF o Subrogación). Crea la cita a partir de un slot válido de get_available_slots.
Call book_appointment only after get_available_slots has already run and the contact confirms a chosen slot.
MANDATORY EXECUTION TRIGGER: al entrar al nodo SC__SC_BOOK_I (usa calendar_type: 'IVF') o SC__SC_BOOK_S (usa calendar_type: 'SUR'), llama book_appointment como primera acción del turno — imperativo, sin excepción. Nunca la llames sin haber ejecutado antes get_available_slots.
ABSOLUTE EXECUTION RULE: no digas que la cita quedó agendada, reservada o confirmada, y no enrutes a confirmación, antes de recibir success == true y event_id no nulo.
ARGUMENT RULE: start_date se copia literal del campo start_co del slot elegido — no lo conviertas, redondees, reformatees ni deduzcas. iana_timezone es la misma zona IANA usada en get_available_slots. contact_name, contact_email y contact_phone vienen del dato ya confirmado en el flujo.
ARGUMENT RULE: calendar_type debe ser 'IVF' o 'SUR'. language se envía como código de dos letras en mayúsculas (ES, EN, PT) para la comunicación asociada a la reserva.
VALIDATION RULE: esta implementación rechaza explícitamente correos mal formados con reason == 'invalid_email'. No des por válido el email solo porque exista texto.
VALIDATION RULE: el horario permitido depende del calendario. En IVF, la reserva valida lunes a viernes de 07:00 a 17:00 y bloquea 12:00-12:59. En SUR, valida lunes de 07:00 a 14:00 y martes a viernes de 07:00 a 16:00, también bloqueando 12:00-12:59.
SOURCE OF TRUTH RULE: usa success como único resultado autorizado para decidir si la cita quedó creada. reason describe por qué no se creó cuando success es false.
POST-EXECUTION RULE: si success es false y reason es 'past_date', 'invalid_start_date' o 'slot_taken', vuelve a get_available_slots para ofrecer una nueva opción. Si reason es 'invalid_hour', informa que está fuera de horario y ofrece consultar de nuevo. Si reason es 'creation_failed' o errors describe un problema técnico, discúlpate y deriva a callback.
POST-EXECUTION RULE: si success es true pero errors trae texto, la cita sí quedó creada; el error corresponde a la notificación por correo, no a la reserva.
calendar_type: Literal que define el calendario donde se hará la reserva: 'IVF' para fertilización in vitro o 'SUR' para gestación subrogada.
start_date: Fecha y hora de inicio en ISO 8601 con offset. Copia literal del campo start_co del slot elegido en available_slots. Ejemplo: 2026-06-04T08:00:00-05:00
duration: Duración de la cita en minutos, como string. Ejemplos habituales del proyecto: '20'.
language: Código de idioma de dos letras en mayúsculas para la notificación y el formateo asociado. Ejemplos: ES, EN, PT.
contact_name: Nombre completo del contacto, ya confirmado en el flujo.
contact_email: Correo del contacto en minúsculas y con formato válido. Se usa para enviar la confirmación de la cita.
contact_phone: Teléfono del contacto en formato internacional. Ejemplo: +573001234567
iana_timezone: Identificador IANA de la zona horaria del contacto, la misma usada para consultar disponibilidad.
```

**Parametros:**

| Nombre | Tipo | Requerido | Enum | Ejemplo | Descripcion |
|---|---|---|---|---|---|
| `calendar_type` | string | si | IVF, SUR | SUR | Literal que define el calendario donde se hará la reserva: 'IVF' para fertilización in vitro o 'SUR' para gestación subrogada. |
| `start_date` | string | si | - | 2026-09-14T07:00:00-05:00 | Fecha y hora de inicio en ISO 8601 con offset. Copia literal del campo start_co del slot elegido en available_slots. Ejemplo: 2026-06-04T08:00:00-05:00 |
| `duration` | string | no | - | 30 | Duración de la cita en minutos, como string. Ejemplos habituales del proyecto: '20'. |
| `language` | string | si | ES, EN, PT | ES | Código de idioma de dos letras en mayúsculas para la notificación y el formateo asociado. Ejemplos: ES, EN, PT. |
| `contact_name` | string | si | - | Tomas Montealegre | Nombre completo del contacto, ya confirmado en el flujo. |
| `contact_email` | string | si | - | tamontealegre@novafem.com.co | Correo del contacto en minúsculas y con formato válido. Se usa para enviar la confirmación de la cita. |
| `contact_phone` | string | si | - | +573007654321 | Teléfono del contacto en formato internacional. Ejemplo: +573001234567 |
| `iana_timezone` | string | no | - | Asia/Jakarta | Identificador IANA de la zona horaria del contacto, la misma usada para consultar disponibilidad. |

<details><summary>JSON schema OpenAI (formato real enviado a bind_tools)</summary>

```json
{
  "type": "function",
  "function": {
    "name": "book_appointment",
    "description": "Herramienta autorizada de reserva para las citas comerciales de Family Aims (IVF o Subrogación). Crea la cita a partir de un slot válido de get_available_slots.\nCall book_appointment only after get_available_slots has already run and the contact confirms a chosen slot.\nMANDATORY EXECUTION TRIGGER: al entrar al nodo SC__SC_BOOK_I (usa calendar_type: 'IVF') o SC__SC_BOOK_S (usa calendar_type: 'SUR'), llama book_appointment como primera acción del turno — imperativo, sin excepción. Nunca la llames sin haber ejecutado antes get_available_slots.\nABSOLUTE EXECUTION RULE: no digas que la cita quedó agendada, reservada o confirmada, y no enrutes a confirmación, antes de recibir success == true y event_id no nulo.\nARGUMENT RULE: start_date se copia literal del campo start_co del slot elegido — no lo conviertas, redondees, reformatees ni deduzcas. iana_timezone es la misma zona IANA usada en get_available_slots. contact_name, contact_email y contact_phone vienen del dato ya confirmado en el flujo.\nARGUMENT RULE: calendar_type debe ser 'IVF' o 'SUR'. language se envía como código de dos letras en mayúsculas (ES, EN, PT) para la comunicación asociada a la reserva.\nVALIDATION RULE: esta implementación rechaza explícitamente correos mal formados con reason == 'invalid_email'. No des por válido el email solo porque exista texto.\nVALIDATION RULE: el horario permitido depende del calendario. En IVF, la reserva valida lunes a viernes de 07:00 a 17:00 y bloquea 12:00-12:59. En SUR, valida lunes de 07:00 a 14:00 y martes a viernes de 07:00 a 16:00, también bloqueando 12:00-12:59.\nSOURCE OF TRUTH RULE: usa success como único resultado autorizado para decidir si la cita quedó creada. reason describe por qué no se creó cuando success es false.\nPOST-EXECUTION RULE: si success es false y reason es 'past_date', 'invalid_start_date' o 'slot_taken', vuelve a get_available_slots para ofrecer una nueva opción. Si reason es 'invalid_hour', informa que está fuera de horario y ofrece consultar de nuevo. Si reason es 'creation_failed' o errors describe un problema técnico, discúlpate y deriva a callback.\nPOST-EXECUTION RULE: si success es true pero errors trae texto, la cita sí quedó creada; el error corresponde a la notificación por correo, no a la reserva.\ncalendar_type: Literal que define el calendario donde se hará la reserva: 'IVF' para fertilización in vitro o 'SUR' para gestación subrogada.\nstart_date: Fecha y hora de inicio en ISO 8601 con offset. Copia literal del campo start_co del slot elegido en available_slots. Ejemplo: 2026-06-04T08:00:00-05:00\nduration: Duración de la cita en minutos, como string. Ejemplos habituales del proyecto: '20'.\nlanguage: Código de idioma de dos letras en mayúsculas para la notificación y el formateo asociado. Ejemplos: ES, EN, PT.\ncontact_name: Nombre completo del contacto, ya confirmado en el flujo.\ncontact_email: Correo del contacto en minúsculas y con formato válido. Se usa para enviar la confirmación de la cita.\ncontact_phone: Teléfono del contacto en formato internacional. Ejemplo: +573001234567\niana_timezone: Identificador IANA de la zona horaria del contacto, la misma usada para consultar disponibilidad.",
    "parameters": {
      "type": "object",
      "properties": {
        "calendar_type": {
          "type": "string",
          "description": "Literal que define el calendario donde se hará la reserva: 'IVF' para fertilización in vitro o 'SUR' para gestación subrogada.",
          "enum": [
            "IVF",
            "SUR"
          ],
          "examples": [
            "SUR"
          ]
        },
        "start_date": {
          "type": "string",
          "description": "Fecha y hora de inicio en ISO 8601 con offset. Copia literal del campo start_co del slot elegido en available_slots. Ejemplo: 2026-06-04T08:00:00-05:00",
          "examples": [
            "2026-09-14T07:00:00-05:00"
          ]
        },
        "duration": {
          "type": "string",
          "description": "Duración de la cita en minutos, como string. Ejemplos habituales del proyecto: '20'.",
          "examples": [
            "30"
          ]
        },
        "language": {
          "type": "string",
          "description": "Código de idioma de dos letras en mayúsculas para la notificación y el formateo asociado. Ejemplos: ES, EN, PT.",
          "enum": [
            "ES",
            "EN",
            "PT"
          ],
          "examples": [
            "ES"
          ]
        },
        "contact_name": {
          "type": "string",
          "description": "Nombre completo del contacto, ya confirmado en el flujo.",
          "examples": [
            "Tomas Montealegre"
          ]
        },
        "contact_email": {
          "type": "string",
          "description": "Correo del contacto en minúsculas y con formato válido. Se usa para enviar la confirmación de la cita.",
          "examples": [
            "tamontealegre@novafem.com.co"
          ]
        },
        "contact_phone": {
          "type": "string",
          "description": "Teléfono del contacto en formato internacional. Ejemplo: +573001234567",
          "examples": [
            "+573007654321"
          ]
        },
        "iana_timezone": {
          "type": "string",
          "description": "Identificador IANA de la zona horaria del contacto, la misma usada para consultar disponibilidad.",
          "examples": [
            "Asia/Jakarta"
          ]
        }
      },
      "required": [
        "calendar_type",
        "start_date",
        "language",
        "contact_name",
        "contact_email",
        "contact_phone"
      ],
      "additionalProperties": false
    }
  }
}
```

</details>

## find_appointment

**Descripcion final (lo que el modelo lee en `function.description`):**

```text
Herramienta autorizada para buscar citas existentes de un contacto por nombre o correo en los calendarios de Family Aims.
Call find_appointment when the contact wants to consult, verify, or know the details of an appointment they believe they have already scheduled.
MANDATORY EXECUTION TRIGGER: ejecútala cuando el contacto quiera consultar, verificar o conocer los detalles de una cita que ya cree haber agendado.
ARGUMENT RULE: debes enviar al menos uno entre contact_name o contact_email. Si ambos llegan vacíos, la búsqueda falla.
ARGUMENT RULE: calendar_type permite filtrar por 'IVF', 'SUR' o buscar en ambos con 'BOTH'. Si no se especifica la intención, usa 'BOTH'.
ARGUMENT RULE: iana_timezone controla el formateo horario del campo start_time. language controla el idioma de la fecha/hora renderizada en la respuesta.
SEARCH RULE: la implementación busca eventos Family Aims en una ventana desde ayer hasta 35 días hacia adelante y puede hacer match por nombre completo, por palabras clave del nombre o por correo en contenido/asistentes.
SOURCE OF TRUTH RULE: el array appointments contiene la lista de citas encontradas. Si success es false, revisa errors en lugar de asumir que appointments vacío equivale a no encontrado.
contact_name: Nombre completo del contacto para realizar la búsqueda.
contact_email: Correo electrónico del contacto para realizar la búsqueda.
calendar_type: Filtro de calendario: 'IVF', 'SUR' o 'BOTH' (defecto) para buscar en ambos.
iana_timezone: Zona horaria IANA para mostrar las horas de las citas encontradas. Defecto: America/Bogota.
language: Código de idioma para formatear la respuesta. Defecto: ES.
```

**Parametros:**

| Nombre | Tipo | Requerido | Enum | Ejemplo | Descripcion |
|---|---|---|---|---|---|
| `contact_name` | string | si | - | Tomas Montealegre | Nombre completo del contacto para realizar la búsqueda. |
| `contact_email` | string | no | - | tamontealegre@novafem.com.co | Correo electrónico del contacto para realizar la búsqueda. |
| `calendar_type` | string | no | IVF, SUR, BOTH | SUR | Filtro de calendario: 'IVF', 'SUR' o 'BOTH' (defecto) para buscar en ambos. |
| `iana_timezone` | string | si | - | Asia/Jakarta | Zona horaria IANA para mostrar las horas de las citas encontradas. Defecto: America/Bogota. |
| `language` | string | no | ES, EN, PT | ES | Código de idioma para formatear la respuesta. Defecto: ES. |

<details><summary>JSON schema OpenAI (formato real enviado a bind_tools)</summary>

```json
{
  "type": "function",
  "function": {
    "name": "find_appointment",
    "description": "Herramienta autorizada para buscar citas existentes de un contacto por nombre o correo en los calendarios de Family Aims.\nCall find_appointment when the contact wants to consult, verify, or know the details of an appointment they believe they have already scheduled.\nMANDATORY EXECUTION TRIGGER: ejecútala cuando el contacto quiera consultar, verificar o conocer los detalles de una cita que ya cree haber agendado.\nARGUMENT RULE: debes enviar al menos uno entre contact_name o contact_email. Si ambos llegan vacíos, la búsqueda falla.\nARGUMENT RULE: calendar_type permite filtrar por 'IVF', 'SUR' o buscar en ambos con 'BOTH'. Si no se especifica la intención, usa 'BOTH'.\nARGUMENT RULE: iana_timezone controla el formateo horario del campo start_time. language controla el idioma de la fecha/hora renderizada en la respuesta.\nSEARCH RULE: la implementación busca eventos Family Aims en una ventana desde ayer hasta 35 días hacia adelante y puede hacer match por nombre completo, por palabras clave del nombre o por correo en contenido/asistentes.\nSOURCE OF TRUTH RULE: el array appointments contiene la lista de citas encontradas. Si success es false, revisa errors en lugar de asumir que appointments vacío equivale a no encontrado.\ncontact_name: Nombre completo del contacto para realizar la búsqueda.\ncontact_email: Correo electrónico del contacto para realizar la búsqueda.\ncalendar_type: Filtro de calendario: 'IVF', 'SUR' o 'BOTH' (defecto) para buscar en ambos.\niana_timezone: Zona horaria IANA para mostrar las horas de las citas encontradas. Defecto: America/Bogota.\nlanguage: Código de idioma para formatear la respuesta. Defecto: ES.",
    "parameters": {
      "type": "object",
      "properties": {
        "contact_name": {
          "type": "string",
          "description": "Nombre completo del contacto para realizar la búsqueda.",
          "examples": [
            "Tomas Montealegre"
          ]
        },
        "contact_email": {
          "type": "string",
          "description": "Correo electrónico del contacto para realizar la búsqueda.",
          "examples": [
            "tamontealegre@novafem.com.co"
          ]
        },
        "calendar_type": {
          "type": "string",
          "description": "Filtro de calendario: 'IVF', 'SUR' o 'BOTH' (defecto) para buscar en ambos.",
          "enum": [
            "IVF",
            "SUR",
            "BOTH"
          ],
          "examples": [
            "SUR"
          ]
        },
        "iana_timezone": {
          "type": "string",
          "description": "Zona horaria IANA para mostrar las horas de las citas encontradas. Defecto: America/Bogota.",
          "examples": [
            "Asia/Jakarta"
          ]
        },
        "language": {
          "type": "string",
          "description": "Código de idioma para formatear la respuesta. Defecto: ES.",
          "enum": [
            "ES",
            "EN",
            "PT"
          ],
          "examples": [
            "ES"
          ]
        }
      },
      "required": [
        "contact_name",
        "iana_timezone"
      ],
      "additionalProperties": false
    }
  }
}
```

</details>

## cancel_appointment

**Descripcion final (lo que el modelo lee en `function.description`):**

```text
Herramienta autorizada para cancelar/eliminar una cita existente en los calendarios de Family Aims.
Call cancel_appointment only when the contact explicitly asks to cancel their appointment and you already have the confirmed event_id.
MANDATORY EXECUTION TRIGGER: ejecútala únicamente cuando el contacto solicite explícitamente cancelar su cita y proporcione o confirme el event_id correcto.
ABSOLUTE EXECUTION RULE: no confirmes la cancelación antes de recibir success == true.
ARGUMENT RULE: event_id debe ser el ID exacto del evento. calendar_type debe ser 'IVF' o 'SUR' según corresponda al calendario donde vive la cita.
SOURCE OF TRUTH RULE: usa success como único veredicto autorizado. message solo aparece en éxito; errors explica fallas como credenciales faltantes, token inválido o event_id inexistente.
event_id: Identificador único del evento de Google Calendar a cancelar.
calendar_type: Tipo de calendario donde reside la cita: 'IVF' o 'SUR'.
iana_timezone: Zona horaria IANA del contacto. Campo aceptado por el schema aunque no cambia la operación de borrado.
```

**Parametros:**

| Nombre | Tipo | Requerido | Enum | Ejemplo | Descripcion |
|---|---|---|---|---|---|
| `event_id` | string | si | - | evt_123 | Identificador único del evento de Google Calendar a cancelar. |
| `calendar_type` | string | si | IVF, SUR | SUR | Tipo de calendario donde reside la cita: 'IVF' o 'SUR'. |
| `iana_timezone` | string | si | - | Asia/Jakarta | Zona horaria IANA del contacto. Campo aceptado por el schema aunque no cambia la operación de borrado. |

<details><summary>JSON schema OpenAI (formato real enviado a bind_tools)</summary>

```json
{
  "type": "function",
  "function": {
    "name": "cancel_appointment",
    "description": "Herramienta autorizada para cancelar/eliminar una cita existente en los calendarios de Family Aims.\nCall cancel_appointment only when the contact explicitly asks to cancel their appointment and you already have the confirmed event_id.\nMANDATORY EXECUTION TRIGGER: ejecútala únicamente cuando el contacto solicite explícitamente cancelar su cita y proporcione o confirme el event_id correcto.\nABSOLUTE EXECUTION RULE: no confirmes la cancelación antes de recibir success == true.\nARGUMENT RULE: event_id debe ser el ID exacto del evento. calendar_type debe ser 'IVF' o 'SUR' según corresponda al calendario donde vive la cita.\nSOURCE OF TRUTH RULE: usa success como único veredicto autorizado. message solo aparece en éxito; errors explica fallas como credenciales faltantes, token inválido o event_id inexistente.\nevent_id: Identificador único del evento de Google Calendar a cancelar.\ncalendar_type: Tipo de calendario donde reside la cita: 'IVF' o 'SUR'.\niana_timezone: Zona horaria IANA del contacto. Campo aceptado por el schema aunque no cambia la operación de borrado.",
    "parameters": {
      "type": "object",
      "properties": {
        "event_id": {
          "type": "string",
          "description": "Identificador único del evento de Google Calendar a cancelar.",
          "examples": [
            "evt_123"
          ]
        },
        "calendar_type": {
          "type": "string",
          "description": "Tipo de calendario donde reside la cita: 'IVF' o 'SUR'.",
          "enum": [
            "IVF",
            "SUR"
          ],
          "examples": [
            "SUR"
          ]
        },
        "iana_timezone": {
          "type": "string",
          "description": "Zona horaria IANA del contacto. Campo aceptado por el schema aunque no cambia la operación de borrado.",
          "examples": [
            "Asia/Jakarta"
          ]
        }
      },
      "required": [
        "event_id",
        "calendar_type",
        "iana_timezone"
      ],
      "additionalProperties": false
    }
  }
}
```

</details>

## edit_appointment

**Descripcion final (lo que el modelo lee en `function.description`):**

```text
Herramienta autorizada para modificar o reprogramar una cita existente en los calendarios de Family Aims.
Call edit_appointment only after confirming an existing event_id and a valid new slot when rescheduling.
MANDATORY EXECUTION TRIGGER: ejecútala cuando el contacto solicite cambiar la fecha, hora o duración de una cita ya agendada.
ABSOLUTE EXECUTION RULE: no confirmes el cambio antes de recibir success == true.
ARGUMENT RULE: event_id debe ser el ID exacto de la cita. calendar_type debe ser 'IVF' o 'SUR'.
ARGUMENT RULE: new_start_date debe ser una fecha ISO 8601 válida. Si se está reprogramando, debe provenir de un slot válido obtenido de get_available_slots. Si solo se cambia la duración, este campo es opcional.
ARGUMENT RULE: new_duration es opcional y se envía como string. Si new_start_date viene informado pero new_duration no, la implementación usa '20' como duración por defecto al recalcular el fin.
VALIDATION RULE: si new_start_date cae en el pasado, fuera del horario permitido o choca con otro evento del mismo calendario, la modificación se rechaza.
POLICY NOTE: la validación horaria vuelve a depender de calendar_type, con la misma diferencia IVF/SUR que book_appointment.
SOURCE OF TRUTH RULE: usa success como único veredicto autorizado. message, new_start_time y new_duration son datos de apoyo cuando el cambio sí se aplicó.
event_id: Identificador único del evento de Google Calendar a modificar.
calendar_type: Tipo de calendario: 'IVF' o 'SUR'.
new_start_date: Nueva fecha y hora en ISO 8601 con offset. Requerido si se cambia el horario.
new_duration: Nueva duración de la cita en minutos, como string. Requerido si se cambia la duración y opcional cuando también cambia el horario.
iana_timezone: Zona horaria IANA del contacto para formatear new_start_time. Defecto: America/Bogota.
language: Código de idioma para formatear la respuesta. Defecto: ES.
```

**Parametros:**

| Nombre | Tipo | Requerido | Enum | Ejemplo | Descripcion |
|---|---|---|---|---|---|
| `event_id` | string | si | - | evt_123 | Identificador único del evento de Google Calendar a modificar. |
| `calendar_type` | string | si | IVF, SUR | SUR | Tipo de calendario: 'IVF' o 'SUR'. |
| `new_start_date` | string | si | - | 2026-09-15T09:30:00-05:00 | Nueva fecha y hora en ISO 8601 con offset. Requerido si se cambia el horario. |
| `new_duration` | string | no | - | 20 | Nueva duración de la cita en minutos, como string. Requerido si se cambia la duración y opcional cuando también cambia el horario. |
| `iana_timezone` | string | no | - | Asia/Jakarta | Zona horaria IANA del contacto para formatear new_start_time. Defecto: America/Bogota. |
| `language` | string | no | EN, ES, PT | ES | Código de idioma para formatear la respuesta. Defecto: ES. |

<details><summary>JSON schema OpenAI (formato real enviado a bind_tools)</summary>

```json
{
  "type": "function",
  "function": {
    "name": "edit_appointment",
    "description": "Herramienta autorizada para modificar o reprogramar una cita existente en los calendarios de Family Aims.\nCall edit_appointment only after confirming an existing event_id and a valid new slot when rescheduling.\nMANDATORY EXECUTION TRIGGER: ejecútala cuando el contacto solicite cambiar la fecha, hora o duración de una cita ya agendada.\nABSOLUTE EXECUTION RULE: no confirmes el cambio antes de recibir success == true.\nARGUMENT RULE: event_id debe ser el ID exacto de la cita. calendar_type debe ser 'IVF' o 'SUR'.\nARGUMENT RULE: new_start_date debe ser una fecha ISO 8601 válida. Si se está reprogramando, debe provenir de un slot válido obtenido de get_available_slots. Si solo se cambia la duración, este campo es opcional.\nARGUMENT RULE: new_duration es opcional y se envía como string. Si new_start_date viene informado pero new_duration no, la implementación usa '20' como duración por defecto al recalcular el fin.\nVALIDATION RULE: si new_start_date cae en el pasado, fuera del horario permitido o choca con otro evento del mismo calendario, la modificación se rechaza.\nPOLICY NOTE: la validación horaria vuelve a depender de calendar_type, con la misma diferencia IVF/SUR que book_appointment.\nSOURCE OF TRUTH RULE: usa success como único veredicto autorizado. message, new_start_time y new_duration son datos de apoyo cuando el cambio sí se aplicó.\nevent_id: Identificador único del evento de Google Calendar a modificar.\ncalendar_type: Tipo de calendario: 'IVF' o 'SUR'.\nnew_start_date: Nueva fecha y hora en ISO 8601 con offset. Requerido si se cambia el horario.\nnew_duration: Nueva duración de la cita en minutos, como string. Requerido si se cambia la duración y opcional cuando también cambia el horario.\niana_timezone: Zona horaria IANA del contacto para formatear new_start_time. Defecto: America/Bogota.\nlanguage: Código de idioma para formatear la respuesta. Defecto: ES.",
    "parameters": {
      "type": "object",
      "properties": {
        "event_id": {
          "type": "string",
          "description": "Identificador único del evento de Google Calendar a modificar.",
          "examples": [
            "evt_123"
          ]
        },
        "calendar_type": {
          "type": "string",
          "description": "Tipo de calendario: 'IVF' o 'SUR'.",
          "enum": [
            "IVF",
            "SUR"
          ],
          "examples": [
            "SUR"
          ]
        },
        "new_start_date": {
          "type": "string",
          "description": "Nueva fecha y hora en ISO 8601 con offset. Requerido si se cambia el horario.",
          "examples": [
            "2026-09-15T09:30:00-05:00"
          ]
        },
        "new_duration": {
          "type": "string",
          "description": "Nueva duración de la cita en minutos, como string. Requerido si se cambia la duración y opcional cuando también cambia el horario.",
          "examples": [
            "20"
          ]
        },
        "iana_timezone": {
          "type": "string",
          "description": "Zona horaria IANA del contacto para formatear new_start_time. Defecto: America/Bogota.",
          "examples": [
            "Asia/Jakarta"
          ]
        },
        "language": {
          "type": "string",
          "description": "Código de idioma para formatear la respuesta. Defecto: ES.",
          "enum": [
            "EN",
            "ES",
            "PT"
          ],
          "examples": [
            "ES"
          ]
        }
      },
      "required": [
        "event_id",
        "calendar_type",
        "new_start_date"
      ],
      "additionalProperties": false
    }
  }
}
```

</details>
