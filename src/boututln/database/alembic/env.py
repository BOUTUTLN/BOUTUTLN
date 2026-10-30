from alembic import context

from boututln.database.schema import metadata

engine = context.config.attributes["engine"]

with engine.begin() as connection:
    context.configure(
        connection=connection,
        target_metadata=metadata,
        render_as_batch=True,  # SQLite-friendly ALTER TABLE for future migrations
    )
    with context.begin_transaction():
        context.run_migrations()
