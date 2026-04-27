package com.discovery.extraction.core;

import com.discovery.extraction.exception.QueryNotAllowedException;
import net.sf.jsqlparser.JSQLParserException;
import net.sf.jsqlparser.expression.Alias;
import net.sf.jsqlparser.expression.AnalyticExpression;
import net.sf.jsqlparser.expression.BinaryExpression;
import net.sf.jsqlparser.expression.CaseExpression;
import net.sf.jsqlparser.expression.CastExpression;
import net.sf.jsqlparser.expression.Expression;
import net.sf.jsqlparser.expression.Function;
import net.sf.jsqlparser.expression.NotExpression;
import net.sf.jsqlparser.expression.Parenthesis;
import net.sf.jsqlparser.expression.WhenClause;
import net.sf.jsqlparser.expression.operators.relational.AndExpression;
import net.sf.jsqlparser.expression.operators.relational.Between;
import net.sf.jsqlparser.expression.operators.relational.ExistsExpression;
import net.sf.jsqlparser.expression.operators.relational.ExpressionList;
import net.sf.jsqlparser.expression.operators.relational.InExpression;
import net.sf.jsqlparser.expression.operators.relational.IsNullExpression;
import net.sf.jsqlparser.expression.operators.relational.LikeExpression;
import net.sf.jsqlparser.expression.operators.relational.OrExpression;
import net.sf.jsqlparser.parser.CCJSqlParserUtil;
import net.sf.jsqlparser.schema.Table;
import net.sf.jsqlparser.statement.Statement;
import net.sf.jsqlparser.statement.select.AllColumns;
import net.sf.jsqlparser.statement.select.AllTableColumns;
import net.sf.jsqlparser.statement.select.FromItem;
import net.sf.jsqlparser.statement.select.Join;
import net.sf.jsqlparser.statement.select.OrderByElement;
import net.sf.jsqlparser.statement.select.ParenthesedSelect;
import net.sf.jsqlparser.statement.select.PlainSelect;
import net.sf.jsqlparser.statement.select.Select;
import net.sf.jsqlparser.statement.select.SelectItem;
import net.sf.jsqlparser.statement.select.SetOperationList;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.util.Arrays;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;

/**
 * Server-side whitelist enforcer for user-supplied extraction queries. Runs
 * BEFORE any database connection is opened.
 *
 * <p>Allowed: {@code SELECT [col_list | *] FROM <single_table_or_view>} with
 * optional {@code WHERE} restricted to static-literal filters, optional
 * {@code ORDER BY}, optional {@code LIMIT}, and optional {@code TABLESAMPLE}.
 *
 * <p>Rejected: JOIN of any kind, GROUP BY, HAVING, DISTINCT, UNION/INTERSECT/
 * EXCEPT, CTEs (WITH), subqueries in SELECT/FROM/WHERE, window functions,
 * aggregates (COUNT, SUM, AVG, MIN, MAX, ARRAY_AGG, STRING_AGG...), all DML
 * and all DDL.
 *
 * <p>{@code information_schema.*} and {@code pg_catalog.*} reads are
 * permitted as ordinary single-table targets (they still must satisfy the
 * no-JOIN / no-subquery rules).
 */
@Component
public class QueryWhitelistValidator {

    private static final Logger log = LoggerFactory.getLogger(QueryWhitelistValidator.class);

    /**
     * Aggregate function names that are unconditionally rejected. Names are
     * compared case-insensitively.
     */
    private static final Set<String> AGGREGATES = new HashSet<>(Arrays.asList(
            "count", "sum", "avg", "min", "max",
            "array_agg", "string_agg", "bool_and", "bool_or",
            "every", "bit_and", "bit_or", "corr",
            "covar_pop", "covar_samp", "regr_avgx", "regr_avgy",
            "regr_count", "regr_intercept", "regr_r2", "regr_slope",
            "regr_sxx", "regr_sxy", "regr_syy",
            "stddev", "stddev_pop", "stddev_samp",
            "variance", "var_pop", "var_samp",
            "median", "percentile_cont", "percentile_disc",
            "json_agg", "jsonb_agg", "json_object_agg", "jsonb_object_agg",
            "xmlagg", "grouping", "mode"
    ));

    /**
     * Validates a query; throws {@link QueryNotAllowedException} on violation.
     */
    public void validate(String query) {
        if (query == null) {
            throw new QueryNotAllowedException("Query is null");
        }
        String trimmed = query.trim();
        if (trimmed.isEmpty()) {
            throw new QueryNotAllowedException("Query is empty");
        }

        // Strip an optional trailing semicolon; reject embedded semicolons to
        // deter multi-statement injection.
        if (trimmed.endsWith(";")) {
            trimmed = trimmed.substring(0, trimmed.length() - 1).trim();
        }
        if (trimmed.contains(";")) {
            throw new QueryNotAllowedException("Multiple statements are not allowed");
        }

        Statement stmt;
        try {
            stmt = CCJSqlParserUtil.parse(trimmed);
        } catch (JSQLParserException e) {
            throw new QueryNotAllowedException("Could not parse SQL: " + e.getMessage(), e);
        }

        if (!(stmt instanceof Select select)) {
            throw new QueryNotAllowedException(
                    "Only SELECT statements are allowed; got " + stmt.getClass().getSimpleName());
        }

        // JSqlParser represents CTEs at the Select level. Use the raw
        // List type so we stay compatible with 4.9 (where WithItem is
        // non-generic) as well as 5.x (where it is generic).
        @SuppressWarnings("rawtypes")
        List withItems = null;
        try {
            withItems = select.getWithItemsList();
        } catch (NoSuchMethodError | AbstractMethodError e) {
            withItems = null; // fall through on older parser versions
        }
        if (withItems != null && !withItems.isEmpty()) {
            throw new QueryNotAllowedException("Common Table Expressions (WITH clauses) are not allowed");
        }

        // Reject SetOperationList (UNION / INTERSECT / EXCEPT) at the top level.
        if (select instanceof SetOperationList) {
            throw new QueryNotAllowedException("UNION / INTERSECT / EXCEPT are not allowed");
        }

        if (!(select instanceof PlainSelect plain)) {
            throw new QueryNotAllowedException(
                    "Only plain SELECT queries are allowed; got " + select.getClass().getSimpleName());
        }
        validatePlain(plain);
        log.debug("Query passed whitelist (length={})", trimmed.length());
    }

    private void validatePlain(PlainSelect plain) {
        if (plain.getDistinct() != null) {
            throw new QueryNotAllowedException("DISTINCT is not allowed");
        }
        List<Join> joins = plain.getJoins();
        if (joins != null && !joins.isEmpty()) {
            throw new QueryNotAllowedException("JOIN of any kind is not allowed");
        }
        if (plain.getGroupBy() != null) {
            throw new QueryNotAllowedException("GROUP BY is not allowed");
        }
        if (plain.getHaving() != null) {
            throw new QueryNotAllowedException("HAVING is not allowed");
        }
        if (plain.getWindowDefinitions() != null && !plain.getWindowDefinitions().isEmpty()) {
            throw new QueryNotAllowedException("Window (WINDOW) clauses are not allowed");
        }

        FromItem from = plain.getFromItem();
        if (from == null) {
            throw new QueryNotAllowedException("FROM clause is required");
        }
        if (from instanceof ParenthesedSelect) {
            throw new QueryNotAllowedException("Subqueries in FROM are not allowed");
        }
        if (!(from instanceof Table table)) {
            throw new QueryNotAllowedException(
                    "Only a single table/view is allowed in FROM; got "
                            + from.getClass().getSimpleName());
        }
        enforceAllowedTable(table);

        @SuppressWarnings({"rawtypes", "unchecked"})
        List items = plain.getSelectItems();
        if (items == null || items.isEmpty()) {
            throw new QueryNotAllowedException("SELECT must have at least one projection");
        }
        for (Object rawItem : items) {
            if (!(rawItem instanceof SelectItem)) {
                continue;
            }
            SelectItem item = (SelectItem) rawItem;
            Object expression = item.getExpression();
            if (expression instanceof AllColumns || expression instanceof AllTableColumns) {
                continue;
            }
            if (expression instanceof Expression expr) {
                walkExpression(expr);
            }
            Alias alias = item.getAlias();
            if (alias != null && alias.getName() != null && alias.getName().contains(";")) {
                throw new QueryNotAllowedException("Semicolon in column alias not allowed");
            }
        }

        Expression where = plain.getWhere();
        if (where != null) {
            walkExpression(where);
        }

        if (plain.getOrderByElements() != null) {
            for (OrderByElement ob : plain.getOrderByElements()) {
                if (ob != null && ob.getExpression() != null) {
                    walkExpression(ob.getExpression());
                }
            }
        }
    }

    private void enforceAllowedTable(Table table) {
        String tableName = table.getName();
        if (tableName == null || tableName.isBlank()) {
            throw new QueryNotAllowedException("Table name is required in FROM");
        }
        // information_schema.*, pg_catalog.* and any user-schema read are
        // all permitted as ordinary single-table targets. No further reject
        // logic here.
    }

    /**
     * Recursive validator that rejects forbidden expression shapes. Written
     * with instanceof rather than JSqlParser's visitor API so that we are
     * resilient to API tweaks between 4.x minor versions.
     */
    private void walkExpression(Expression expr) {
        if (expr == null) {
            return;
        }
        if (expr instanceof ParenthesedSelect) {
            throw new QueryNotAllowedException("Subqueries are not allowed");
        }
        if (expr instanceof Select) {
            // Scalar subquery at expression position.
            throw new QueryNotAllowedException("Subqueries are not allowed");
        }
        if (expr instanceof AnalyticExpression) {
            throw new QueryNotAllowedException("Window / analytic functions are not allowed");
        }
        if (expr instanceof ExistsExpression) {
            throw new QueryNotAllowedException("EXISTS subqueries are not allowed");
        }
        if (expr instanceof Function fn) {
            String name = fn.getName();
            if (name != null && AGGREGATES.contains(name.toLowerCase(Locale.ROOT))) {
                throw new QueryNotAllowedException("Aggregate function not allowed: " + name);
            }
            if (fn.isDistinct()) {
                throw new QueryNotAllowedException("DISTINCT inside function call is not allowed");
            }
            Object params = fn.getParameters();
            if (params instanceof ExpressionList el) {
                for (Object p : el) {
                    if (p instanceof Expression e) {
                        walkExpression(e);
                    }
                }
            } else if (params instanceof Iterable<?> it) {
                for (Object p : it) {
                    if (p instanceof Expression e) {
                        walkExpression(e);
                    }
                }
            }
            return;
        }
        if (expr instanceof InExpression in) {
            Expression right = in.getRightExpression();
            if (right instanceof ParenthesedSelect || right instanceof Select) {
                throw new QueryNotAllowedException("IN (subquery) is not allowed");
            }
            walkExpression(in.getLeftExpression());
            walkExpression(right);
            return;
        }
        if (expr instanceof Between b) {
            walkExpression(b.getLeftExpression());
            walkExpression(b.getBetweenExpressionStart());
            walkExpression(b.getBetweenExpressionEnd());
            return;
        }
        if (expr instanceof IsNullExpression isn) {
            walkExpression(isn.getLeftExpression());
            return;
        }
        if (expr instanceof LikeExpression like) {
            walkExpression(like.getLeftExpression());
            walkExpression(like.getRightExpression());
            return;
        }
        if (expr instanceof NotExpression not) {
            walkExpression(not.getExpression());
            return;
        }
        if (expr instanceof Parenthesis par) {
            walkExpression(par.getExpression());
            return;
        }
        if (expr instanceof CastExpression cast) {
            walkExpression(cast.getLeftExpression());
            return;
        }
        if (expr instanceof CaseExpression caseExpr) {
            walkExpression(caseExpr.getSwitchExpression());
            if (caseExpr.getWhenClauses() != null) {
                for (WhenClause w : caseExpr.getWhenClauses()) {
                    walkExpression(w.getWhenExpression());
                    // JSqlParser renamed getThenExpression() to
                    // getThenExpression / getThenStatement across minors;
                    // call whichever is available via reflection.
                    walkExpression(extractThenExpression(w));
                }
            }
            walkExpression(caseExpr.getElseExpression());
            return;
        }
        if (expr instanceof AndExpression and) {
            walkExpression(and.getLeftExpression());
            walkExpression(and.getRightExpression());
            return;
        }
        if (expr instanceof OrExpression or) {
            walkExpression(or.getLeftExpression());
            walkExpression(or.getRightExpression());
            return;
        }
        if (expr instanceof BinaryExpression bin) {
            walkExpression(bin.getLeftExpression());
            walkExpression(bin.getRightExpression());
            return;
        }
        if (expr instanceof ExpressionList list) {
            for (Object e : list) {
                if (e instanceof Expression child) {
                    walkExpression(child);
                }
            }
            return;
        }
        // Columns, JdbcParameter, literal values, etc. are all fine here:
        // they cannot contain forbidden sub-structures.
    }

    private static Expression extractThenExpression(WhenClause w) {
        try {
            java.lang.reflect.Method m = w.getClass().getMethod("getThenExpression");
            Object v = m.invoke(w);
            return v instanceof Expression e ? e : null;
        } catch (ReflectiveOperationException ignored) {
            // fall through
        }
        try {
            java.lang.reflect.Method m = w.getClass().getMethod("getThenStatement");
            Object v = m.invoke(w);
            return v instanceof Expression e ? e : null;
        } catch (ReflectiveOperationException ignored) {
            return null;
        }
    }
}
