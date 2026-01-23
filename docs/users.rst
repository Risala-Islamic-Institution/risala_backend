User Model Configuration
------------------------

The custom User model (`risala_backend.users.models.User`) has been enhanced to support:

*   **UUID Primary Key**: Uses a UUID instead of an integer ID for better security and scalability.
*   **Roles**: A `role` field to distinguish between `ADMIN`, `STUDENT`, `INSTRUCTOR`, `SUPPORT`, and `FINANCE`.
*   **Timestamps**: Automatic `created_at` and `updated_at` tracking.

.. automodule:: risala_backend.users.models
   :members:
   :noindex:

