# Crear o actualizar un usuario de Mailing-lists

> ⚠️ **Pendiente:** el login de Mailing-lists reparte acceso a información
> importante (listas de correo y de acceso a otras herramientas) y hoy no está
> lo bastante protegido. Reforzarlo está pendiente de cambios por Pablo.

## Pasos

1. Abrir WSL.

2. Activar la venv de WSL usada para Mailing-lists:

   ```bash
   source ~/mailinglists-venv/bin/activate
   ```

3. Ir a la carpeta del proyecto:

   ```bash
   cd "/mnt/c/Users/Pablo Coma/Desktop/Proyectos/Proan/proan-Otros/Mailing-lists"
   ```

4. Si hace falta, comprobar que las credenciales ADC siguen disponibles:

   ```bash
   gcloud auth application-default login
   ```

   Si no pide nada y ya está autenticado, continuar.

5. Crear o actualizar el usuario:

   ```bash
   python create_or_update_user.py --username NOMBRE_USUARIO --password CONTRASENA
   ```

   Ejemplo:

   ```bash
   python create_or_update_user.py --username admin --password MiPassword123
   ```

6. Si se quiere dejar el usuario inactivo:

   ```bash
   python create_or_update_user.py --username NOMBRE_USUARIO --password CONTRASENA --inactive
   ```

7. El script guarda o actualiza el documento en Firestore, en la colección `auth_users`.

8. Campos que quedan guardados:

   - `username`
   - `password_hash`
   - `active`
   - `created_at`
   - `updated_at`

9. La contraseña no se guarda en claro. Solo se guarda el hash.

10. Si se quiere cambiar la contraseña de un usuario existente, ejecutar otra vez el mismo comando con la nueva contraseña.

11. Para salir de la venv:

    ```bash
    deactivate
    ```
