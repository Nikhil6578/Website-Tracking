
#### To run DB migration for the web_snapshot app, use below command:
 
 `python manage.py migrate web_snapshot --database=web_snapshot`


##### Run below script to create web_snapshot DB and its Role for the first time:

    
    CREATE ROLE django_test_app WITH PASSWORD 'django';
    CREATE ROLE testro WITH PASSWORD 'testro';
    CREATE ROLE web_snapshot_app WITH PASSWORD 'django';
    CREATE ROLE cfyreadaccess WITH PASSWORD 'c0ntifY4$$$$';
    
    DROP DATABASE web_snapshot_db;
    CREATE DATABASE web_snapshot_db OWNER web_snapshot_app;
    GRANT ALL PRIVILEGES ON DATABASE web_snapshot_db to web_snapshot_app;
    ALTER ROLE web_snapshot_app WITH login;
    \connect web_snapshot_db
    CREATE EXTENSION hstore;
    CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA pg_catalog;
    CREATE EXTENSION IF NOT EXISTS postgres_fdw;
    GRANT USAGE on FOREIGN DATA WRAPPER postgres_fdw to web_snapshot_app;
    GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO web_snapshot_app;
    GRANT ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public TO web_snapshot_app;
    GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO web_snapshot_app;
    GRANT SELECT ON ALL TABLES IN SCHEMA public TO cfyreadaccess;


##### Initial data migration:

    /usr/pgsql-11/bin/pg_dump -Z 9 -S postgres -n public -Fc contify_db --table website_tracking_websnapshot --table website_tracking_diffhtml --table website_tracking_diffcontent > wst_websnapshot
    
    /usr/pgsql-11/bin/pg_restore -d web_snapshot_db -U web_snapshot_app -S postgres --no-owner --exit-on-error --single-transaction --schema-only --disable-triggers -Fc wst_websnapshot -n public
    
    /usr/pgsql-11/bin/pg_restore -d web_snapshot_db -U postgres -S postgres --jobs=8 --data-only --disable-triggers -Fc wst_websnapshot
    
    
    INSERT INTO web_snapshot_websnapshot (id, web_source_id, state, status, hash_html, raw_html, raw_snapshot, last_error, created_on, updated_on) (SELECT id, web_source_id, state, status, hash_html, raw_html, raw_snapshot, last_error, created_on, updated_on FROM website_tracking_websnapshot);
    ALTER sequence web_snapshot_websnapshot_id_seq RESTART WITH 28;
    
    
    INSERT INTO web_snapshot_diffhtml (id, old_web_snapshot_id, old_diff_html, removed_diff_info, new_web_snapshot_id, new_diff_html, added_diff_info, state, status, last_error, created_on, updated_on) (SELECT id, old_web_snapshot_id, old_diff_html, removed_diff_info, new_web_snapshot_id, new_diff_html, added_diff_info, state, status, last_error, created_on, updated_on FROM website_tracking_diffhtml);
    alter sequence web_snapshot_diffhtml_id_seq restart with 39;
    
    
    INSERT INTO web_snapshot_diffcontent (id, old_snapshot_id, old_diff_html, old_diff_image, new_snapshot_id, new_diff_html, new_diff_image, state, status, added_diff_info, removed_diff_info, created_on, updated_on) (SELECT id, old_snapshot_id, old_diff_html, old_diff_image, new_snapshot_id, new_diff_html, new_diff_image, state, status, added_diff_info, removed_diff_info, created_on, updated_on FROM website_tracking_diffcontent);
    alter sequence web_snapshot_diffcontent_id_seq restart with 59;
    
    
    GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO web_snapshot_app;
    GRANT ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public TO web_snapshot_app;
    GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO web_snapshot_app;
    GRANT SELECT ON ALL TABLES IN SCHEMA public TO cfyreadaccess;
