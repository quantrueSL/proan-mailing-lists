# Listas y accesos

App Flask para gestionar dos cosas que viven en el mismo Firestore:

- **Listas de correo** (`kind: "mailing"`): destinatarios de avisos automáticos,
  como `cambio_divisa`.
- **Listas de acceso** (`kind: "access"`): quién puede entrar en una herramienta
  y con qué rol. Hoy la usa `proan-Hidrocarburos` (ver su `LOGIN.md`).

La interfaz separa las dos en secciones distintas, porque el rol solo significa
algo en las de acceso.

## Requisitos y ejecución en local

- Python 3.11+ y las dependencias de `requirements.txt`.
- Un `.env` (parte de `.env.example`) con `FLASK_SECRET_KEY`, `GCP_PROJECT` y
  `FIRESTORE_DATABASE_ID`.
- Credenciales de Google Cloud con acceso a Firestore:
  `gcloud auth application-default login`.

```bash
pip install -r requirements.txt
cp .env.example .env   # y rellena FLASK_SECRET_KEY
python main.py          # sirve en http://localhost:8080
```

## Crear un usuario

El login no tiene alta desde la interfaz: los usuarios se crean o actualizan
con un script. Pasos en
[`crearnuevousuariomailinglist.md`](crearnuevousuariomailinglist.md).

## Persistencia

- Proyecto: `proan-quantrue`
- Base Firestore: `proan-lista-mails`
- Colección principal: `lists`
- Documento por lista: `lists/{list_id}`
- Historial: subcolección `lists/{list_id}/history`, una entrada por guardado

## Campos del documento

| Campo | Tipo | Notas |
|---|---|---|
| `name` | string | Editable. Se puede renombrar. |
| `emails` | array de strings | Quién recibe el aviso, o quién puede entrar. |
| `roles` | map correo → rol | Solo en listas de acceso. Valores: `gerencia`, `generico`. |
| `kind` | string | `mailing` (por defecto si falta) o `access`. Se fija al crear. |
| `enabled` | bool | Una lista activa necesita al menos un correo. |
| `comment` | string | |
| `updated_at` | timestamp | |
| `updated_by` | string | **Del usuario de la sesión**, no del cuerpo de la petición. |

Reglas del modelo:

- **`emails` es la puerta y `roles` solo reparte permisos.** Un correo con rol que
  no esté en `emails` se descarta al guardar; quitar un correo se lleva su rol.
- Correo en `emails` sin entrada en `roles` → `generico`. El rol se concede, nunca
  se hereda.
- Un valor de rol irreconocible degrada a `generico`: una errata quita permisos,
  nunca los concede.
- `save_list` escribe el documento completo con `set()` sin `merge`, porque es la
  única forma de que quitar un correo o un rol surta efecto. **Si se añade un campo
  nuevo al modelo, hay que añadirlo también a `firestore_payload`**, o se perderá
  en cada guardado. Los campos que un cliente puede no enviar (`roles`, `kind`) se
  conservan del documento existente: se distingue "no me lo has mandado" de
  "vacíamelo".

## Endpoints

| Método | Ruta | |
|---|---|---|
| GET | `/health` | |
| GET | `/` | Interfaz |
| GET | `/login` | |
| POST | `/api/auth/login` | |
| POST | `/api/auth/logout` | |
| GET | `/api/session` | Quién está conectado |
| GET | `/api/lists` | |
| GET | `/api/lists/<list_id>` | |
| POST | `/api/lists/<list_id>` | Crear o actualizar |
| DELETE | `/api/lists/<list_id>` | Borra la lista y su historial. No expuesto en la interfaz |
| GET | `/api/lists/<list_id>/history` | |

## Crear una lista

**Las listas se crean en la consola de Firestore, no en la interfaz.** Es una
operación estructural y poco frecuente: una lista nueva no sirve de nada hasta que
alguna herramienta la lee, así que crearla desde la interfaz sería media acción.
La app gestiona los miembros de las listas que ya existen.

En `proan-lista-mails` → colección `lists` → *Agregar documento*, con el
identificador como ID (minúsculas, números, `-` y `_`) y estos campos:

| Campo | Tipo | Valor |
|---|---|---|
| `name` | string | el nombre visible |
| `emails` | array | un string por correo |
| `kind` | string | `access` o `mailing` |
| `enabled` | boolean | `true` |
| `roles` | map | solo si es de acceso: correo → `gerencia` \| `generico` |

**No olvides `kind`.** Sin ese campo la lista se interpreta como `mailing` y
aparecerá en la sección de correo, sin desplegable de rol. `comment`,
`updated_at` y `updated_by` los rellena la app en el primer guardado.

### Borrar una lista

Tampoco se hace desde la interfaz, por la misma razón. Pero ojo con hacerlo en la
consola: **Firestore no borra las subcolecciones en cascada**, así que borrar el
documento deja el `history` huérfano — invisible en la consola y ocupando espacio.

Para un borrado limpio existe el endpoint, que borra primero el historial. No está
expuesto en la interfaz a propósito:

```bash
curl -X DELETE https://<url-del-servicio>/api/lists/<list_id> -b cookies.txt
```

Necesita la cookie de una sesión iniciada.

## Interfaz

- Dos secciones: **Listas de correo** y **Accesos**, según el campo `kind`.
- El **identificador** es el ID del documento y no cambia. El **nombre** se puede
  cambiar siempre.
- Los correos son campos editables directamente, con validación por fila:
  dirección mal escrita o repetida impide guardar y señala la fila; una fila vacía
  solo avisa de que se descartará.
- **Guardar** está desactivado si no has tocado nada, y cambiar de lista o de
  sección con cambios pendientes pide confirmación.
- Buscador de listas, historial de cambios desplegable y borrado con confirmación.

## Ejemplo de payload

```json
{
  "name": "Acceso Hidrocarburos",
  "emails": ["alguien@proan.com", "otro@proan.com"],
  "roles": { "alguien@proan.com": "gerencia" },
  "kind": "access",
  "enabled": true,
  "comment": ""
}
```

`updated_by` no se manda: lo pone el servidor desde la sesión.

## Deploy

1. Crea un `.env` local con `FLASK_SECRET_KEY`.
2. Ejecuta `bash deploy.sh`.

**No metas comentarios entre las líneas del `gcloud run deploy`.** Cada línea acaba
en `\` para continuar el comando, y un `#` en medio lo corta ahí: se desplegaría
con la mitad de las opciones y sin avisar. Los comentarios, encima del comando.

## Pendiente

El servicio está desplegado con `--allow-unauthenticated` y su login no tiene
límite de intentos. Mientras solo gestionaba destinatarios de correo era poco
relevante; ahora que reparte permisos de una herramienta, quien entre aquí puede
concederse el rol de gerencia. Ver el apartado correspondiente en el `LOGIN.md`
de `proan-Hidrocarburos`.
