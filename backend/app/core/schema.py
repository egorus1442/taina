from sqlalchemy import inspect, text

from app.core.database import engine


def ensure_database_schema() -> None:
    """Apply small compatibility upgrades for the current no-migrations setup."""
    if engine.dialect.name != "sqlite":
        return

    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if "sources" not in table_names:
        return

    with engine.begin() as connection:
        if "source_groups" not in table_names:
            connection.execute(
                text(
                    """
                    CREATE TABLE source_groups (
                        id INTEGER NOT NULL,
                        title VARCHAR(255) NOT NULL,
                        channel_source_id INTEGER,
                        chat_source_id INTEGER,
                        city VARCHAR(128) NOT NULL,
                        region VARCHAR(128),
                        enabled BOOLEAN NOT NULL DEFAULT 1,
                        monitor_channel_posts BOOLEAN NOT NULL DEFAULT 1,
                        monitor_chat_messages BOOLEAN NOT NULL DEFAULT 1,
                        monitor_channel_comments BOOLEAN NOT NULL DEFAULT 0,
                        comments_fetch_mode VARCHAR(32) NOT NULL DEFAULT 'off',
                        comments_poll_interval_seconds INTEGER NOT NULL DEFAULT 5,
                        comments_last_posts_limit INTEGER NOT NULL DEFAULT 5,
                        comments_last_checked_at DATETIME,
                        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (id),
                        FOREIGN KEY(channel_source_id) REFERENCES sources (id),
                        FOREIGN KEY(chat_source_id) REFERENCES sources (id)
                    )
                    """
                )
            )
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_source_groups_id ON source_groups (id)"))
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_source_groups_channel_source_id "
                    "ON source_groups (channel_source_id)"
                )
            )
            connection.execute(
                text("CREATE INDEX IF NOT EXISTS ix_source_groups_chat_source_id ON source_groups (chat_source_id)")
            )
        group_count = connection.execute(text("SELECT COUNT(*) FROM source_groups")).scalar() or 0
        source_count = connection.execute(text("SELECT COUNT(*) FROM sources")).scalar() or 0
        if group_count == 0 and source_count:
            connection.execute(
                text(
                    """
                    INSERT INTO source_groups (
                        title,
                        channel_source_id,
                        chat_source_id,
                        city,
                        region,
                        enabled,
                        monitor_channel_posts,
                        monitor_chat_messages,
                        monitor_channel_comments,
                        comments_fetch_mode,
                        comments_poll_interval_seconds,
                        comments_last_posts_limit,
                        created_at,
                        updated_at
                    )
                    SELECT
                        title,
                        CASE WHEN type = 'channel' THEN id ELSE NULL END,
                        CASE WHEN type = 'chat' THEN id ELSE NULL END,
                        city,
                        region,
                        enabled,
                        CASE WHEN type = 'channel' THEN 1 ELSE 0 END,
                        CASE WHEN type = 'chat' THEN 1 ELSE 0 END,
                        0,
                        'off',
                        5,
                        5,
                        CURRENT_TIMESTAMP,
                        CURRENT_TIMESTAMP
                    FROM sources
                    """
                )
            )
        if "comment_thread_states" not in table_names:
            connection.execute(
                text(
                    """
                    CREATE TABLE comment_thread_states (
                        id INTEGER NOT NULL,
                        source_group_id INTEGER NOT NULL,
                        channel_source_id INTEGER NOT NULL,
                        channel_post_id INTEGER NOT NULL,
                        discussion_chat_id VARCHAR(128),
                        discussion_root_msg_id INTEGER,
                        last_comment_id INTEGER NOT NULL DEFAULT 0,
                        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (id),
                        CONSTRAINT uq_comment_thread_state UNIQUE (
                            source_group_id,
                            channel_source_id,
                            channel_post_id
                        ),
                        FOREIGN KEY(source_group_id) REFERENCES source_groups (id),
                        FOREIGN KEY(channel_source_id) REFERENCES sources (id)
                    )
                    """
                )
            )
            connection.execute(
                text("CREATE INDEX IF NOT EXISTS ix_comment_thread_states_id ON comment_thread_states (id)")
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_comment_thread_states_source_group_id "
                    "ON comment_thread_states (source_group_id)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_comment_thread_states_channel_source_id "
                    "ON comment_thread_states (channel_source_id)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_comment_thread_states_channel_post_id "
                    "ON comment_thread_states (channel_post_id)"
                )
            )
        if "app_settings" not in table_names:
            connection.execute(
                text(
                    """
                    CREATE TABLE app_settings (
                        id INTEGER NOT NULL,
                        key VARCHAR(128) NOT NULL,
                        value TEXT NOT NULL,
                        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (id),
                        UNIQUE (key)
                    )
                    """
                )
            )
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_app_settings_id ON app_settings (id)"))
            connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_app_settings_key ON app_settings (key)"))

    if "events" not in table_names:
        return

    event_columns = {column["name"] for column in inspector.get_columns("events")}
    with engine.begin() as connection:
        if "cluster_id" not in event_columns:
            connection.execute(text("ALTER TABLE events ADD COLUMN cluster_id INTEGER"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_events_cluster_id ON events (cluster_id)"))
        if "review_status" not in event_columns:
            connection.execute(
                text("ALTER TABLE events ADD COLUMN review_status VARCHAR(32) NOT NULL DEFAULT 'auto_approved'")
            )
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_events_review_status ON events (review_status)"))
        if "review_reason" not in event_columns:
            connection.execute(text("ALTER TABLE events ADD COLUMN review_reason TEXT"))
