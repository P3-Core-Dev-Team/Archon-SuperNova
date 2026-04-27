package com.discovery.extraction;

import com.discovery.extraction.exception.QueryNotAllowedException;
import com.discovery.extraction.core.QueryWhitelistValidator;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

import static org.assertj.core.api.Assertions.assertThatCode;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * Parametric tests for {@link QueryWhitelistValidator}. Covers each
 * rejection category with at least one representative case, plus an
 * allow-list covering simple selects, WHERE filters, information_schema
 * and pg_catalog reads, ORDER BY, LIMIT, and TABLESAMPLE.
 */
class QueryWhitelistValidatorTest {

    private final QueryWhitelistValidator validator = new QueryWhitelistValidator();

    // --- Allowed ---

    @ParameterizedTest
    @ValueSource(strings = {
            "SELECT * FROM users",
            "SELECT id, name FROM public.users",
            "SELECT * FROM public.users WHERE id = 1",
            "SELECT id FROM users WHERE status = 'active' AND created_at > '2024-01-01'",
            "SELECT * FROM users WHERE id IN (1, 2, 3)",
            "SELECT id FROM users ORDER BY id LIMIT 100",
            "SELECT * FROM information_schema.tables WHERE table_schema = 'public'",
            "SELECT * FROM pg_catalog.pg_tables WHERE schemaname = 'public'",
            "SELECT column_name, data_type FROM information_schema.columns",
            "SELECT * FROM sales TABLESAMPLE BERNOULLI(10)",
            "SELECT id, CAST(amount AS VARCHAR) FROM orders",
    })
    void allowsWhitelistedQueries(String query) {
        assertThatCode(() -> validator.validate(query))
                .as("query should be accepted: %s", query)
                .doesNotThrowAnyException();
    }

    // --- Rejected ---

    @Test
    void rejectsNullQuery() {
        assertThatThrownBy(() -> validator.validate(null))
                .isInstanceOf(QueryNotAllowedException.class);
    }

    @Test
    void rejectsEmptyQuery() {
        assertThatThrownBy(() -> validator.validate("   "))
                .isInstanceOf(QueryNotAllowedException.class);
    }

    @Test
    void rejectsMultipleStatements() {
        assertThatThrownBy(() -> validator.validate(
                "SELECT * FROM users; DROP TABLE users"))
                .isInstanceOf(QueryNotAllowedException.class)
                .hasMessageContaining("Multiple statements");
    }

    @ParameterizedTest
    @ValueSource(strings = {
            "SELECT * FROM users JOIN orders ON users.id = orders.user_id",
            "SELECT * FROM users u INNER JOIN orders o ON u.id = o.user_id",
            "SELECT u.id FROM users u LEFT JOIN orders o ON u.id = o.user_id",
            "SELECT * FROM users CROSS JOIN orders",
            "SELECT * FROM users NATURAL JOIN orders",
    })
    void rejectsJoins(String query) {
        assertThatThrownBy(() -> validator.validate(query))
                .isInstanceOf(QueryNotAllowedException.class)
                .hasMessageContaining("JOIN");
    }

    @Test
    void rejectsGroupBy() {
        assertThatThrownBy(() -> validator.validate(
                "SELECT user_id FROM orders GROUP BY user_id"))
                .isInstanceOf(QueryNotAllowedException.class)
                .hasMessageContaining("GROUP BY");
    }

    @Test
    void rejectsHaving() {
        // HAVING without GROUP BY still parses; tests that we catch it.
        assertThatThrownBy(() -> validator.validate(
                "SELECT user_id FROM orders GROUP BY user_id HAVING user_id > 0"))
                .isInstanceOf(QueryNotAllowedException.class);
    }

    @ParameterizedTest
    @ValueSource(strings = {
            "SELECT COUNT(*) FROM users",
            "SELECT SUM(amount) FROM orders",
            "SELECT AVG(amount) FROM orders",
            "SELECT MIN(id), MAX(id) FROM users",
            "SELECT ARRAY_AGG(name) FROM users",
            "SELECT STRING_AGG(name, ',') FROM users",
    })
    void rejectsAggregates(String query) {
        assertThatThrownBy(() -> validator.validate(query))
                .isInstanceOf(QueryNotAllowedException.class)
                .hasMessageContaining("Aggregate");
    }

    @Test
    void rejectsDistinct() {
        assertThatThrownBy(() -> validator.validate(
                "SELECT DISTINCT user_id FROM orders"))
                .isInstanceOf(QueryNotAllowedException.class)
                .hasMessageContaining("DISTINCT");
    }

    @Test
    void rejectsSubqueryInFrom() {
        assertThatThrownBy(() -> validator.validate(
                "SELECT * FROM (SELECT id FROM users) u"))
                .isInstanceOf(QueryNotAllowedException.class)
                .hasMessageContaining("Subqueries in FROM");
    }

    @Test
    void rejectsSubqueryInWhere() {
        assertThatThrownBy(() -> validator.validate(
                "SELECT id FROM users WHERE id IN (SELECT user_id FROM orders)"))
                .isInstanceOf(QueryNotAllowedException.class)
                .hasMessageContaining("IN (subquery)");
    }

    @Test
    void rejectsExistsSubquery() {
        assertThatThrownBy(() -> validator.validate(
                "SELECT id FROM users WHERE EXISTS (SELECT 1 FROM orders)"))
                .isInstanceOf(QueryNotAllowedException.class)
                .hasMessageContaining("EXISTS");
    }

    @Test
    void rejectsCte() {
        assertThatThrownBy(() -> validator.validate(
                "WITH u AS (SELECT id FROM users) SELECT * FROM u"))
                .isInstanceOf(QueryNotAllowedException.class)
                .hasMessageContaining("Common Table Expressions");
    }

    @ParameterizedTest
    @ValueSource(strings = {
            "SELECT id FROM users UNION SELECT id FROM admins",
            "SELECT id FROM users UNION ALL SELECT id FROM admins",
            "SELECT id FROM users INTERSECT SELECT id FROM admins",
            "SELECT id FROM users EXCEPT SELECT id FROM admins",
    })
    void rejectsSetOperations(String query) {
        assertThatThrownBy(() -> validator.validate(query))
                .isInstanceOf(QueryNotAllowedException.class);
    }

    @Test
    void rejectsWindowFunctions() {
        assertThatThrownBy(() -> validator.validate(
                "SELECT ROW_NUMBER() OVER (ORDER BY id) FROM users"))
                .isInstanceOf(QueryNotAllowedException.class)
                .hasMessageContaining("Window");
    }

    @ParameterizedTest
    @ValueSource(strings = {
            "INSERT INTO users (id, name) VALUES (1, 'x')",
            "UPDATE users SET name = 'x' WHERE id = 1",
            "DELETE FROM users WHERE id = 1",
    })
    void rejectsDml(String query) {
        assertThatThrownBy(() -> validator.validate(query))
                .isInstanceOf(QueryNotAllowedException.class);
    }

    @ParameterizedTest
    @ValueSource(strings = {
            "CREATE TABLE test (id INT)",
            "DROP TABLE users",
            "ALTER TABLE users ADD COLUMN foo TEXT",
            "TRUNCATE TABLE users",
    })
    void rejectsDdl(String query) {
        assertThatThrownBy(() -> validator.validate(query))
                .isInstanceOf(QueryNotAllowedException.class);
    }

    @Test
    void rejectsCountDistinctInProjection() {
        assertThatThrownBy(() -> validator.validate(
                "SELECT COUNT(DISTINCT user_id) FROM orders"))
                .isInstanceOf(QueryNotAllowedException.class);
    }

    @Test
    void rejectsMalformedSql() {
        assertThatThrownBy(() -> validator.validate("SELECT FROM"))
                .isInstanceOf(QueryNotAllowedException.class)
                .hasMessageContaining("Could not parse SQL");
    }

    @Test
    void rejectsSemicolonInAlias() {
        // JSqlParser generally accepts double-quoted identifiers; an embedded
        // semicolon is the "smuggler" case we guard against.
        assertThatThrownBy(() -> validator.validate(
                "SELECT id AS \"a;b\" FROM users"))
                .isInstanceOf(QueryNotAllowedException.class);
    }

    @Test
    void rejectsEmbeddedSemicolonInBody() {
        assertThatThrownBy(() -> validator.validate(
                "SELECT id FROM users; DELETE FROM users"))
                .isInstanceOf(QueryNotAllowedException.class);
    }
}
