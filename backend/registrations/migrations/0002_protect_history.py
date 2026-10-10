from django.db import migrations


FORWARD = """
CREATE FUNCTION chags_guard_price() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(NEW.category_id, NEW.amount, NEW.currency, NEW.valid_from)
       IS DISTINCT FROM ROW(OLD.category_id, OLD.amount, OLD.currency, OLD.valid_from) THEN
        RAISE EXCEPTION 'Price commercial fields are immutable; create a new price'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER chags_price_history BEFORE UPDATE ON registrations_registrationprice
FOR EACH ROW EXECUTE FUNCTION chags_guard_price();

CREATE FUNCTION chags_guard_category() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.event_id IS DISTINCT FROM OLD.event_id THEN
        RAISE EXCEPTION 'A category cannot be moved to another event' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER chags_category_event BEFORE UPDATE ON registrations_registrationcategory
FOR EACH ROW EXECUTE FUNCTION chags_guard_category();

CREATE FUNCTION chags_guard_registration() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF ROW(NEW.user_id, NEW.event_id, NEW.category_id, NEW.source_price_id,
               NEW.amount, NEW.currency, NEW.category_name, NEW.review_required, NEW.created_at)
           IS DISTINCT FROM ROW(OLD.user_id, OLD.event_id, OLD.category_id, OLD.source_price_id,
               OLD.amount, OLD.currency, OLD.category_name, OLD.review_required, OLD.created_at) THEN
            RAISE EXCEPTION 'Registration owner and price snapshot are immutable' USING ERRCODE = '23514';
        END IF;
    ELSE
        IF NEW.status <> 'pending' OR NOT EXISTS (
            SELECT 1 FROM registrations_registrationprice p
            JOIN registrations_registrationcategory c ON c.id = p.category_id
            WHERE p.id = NEW.source_price_id AND c.id = NEW.category_id
              AND c.event_id = NEW.event_id AND p.amount = NEW.amount
              AND p.currency = NEW.currency AND c.name_pt = NEW.category_name
              AND c.requires_review = NEW.review_required
              AND NEW.category_review = CASE WHEN c.requires_review THEN 'pending' ELSE 'not_required' END
        ) THEN
            RAISE EXCEPTION 'Registration must start pending with a matching price and category'
                USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER chags_registration_history BEFORE INSERT OR UPDATE ON registrations_registration
FOR EACH ROW EXECUTE FUNCTION chags_guard_registration();
"""

REVERSE = """
DROP TRIGGER chags_registration_history ON registrations_registration;
DROP FUNCTION chags_guard_registration();
DROP TRIGGER chags_category_event ON registrations_registrationcategory;
DROP FUNCTION chags_guard_category();
DROP TRIGGER chags_price_history ON registrations_registrationprice;
DROP FUNCTION chags_guard_price();
"""


class Migration(migrations.Migration):
    dependencies = [("registrations", "0001_initial")]
    operations = [migrations.RunSQL(FORWARD, REVERSE)]
