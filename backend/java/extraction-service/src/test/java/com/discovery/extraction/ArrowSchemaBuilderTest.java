package com.discovery.extraction;

import com.discovery.extraction.parquet.ArrowSchemaBuilder;
import org.apache.arrow.vector.types.pojo.ArrowType;
import org.junit.jupiter.api.Test;

import java.sql.Types;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * One assertion per mapped JDBC type. All common Postgres types and
 * vendor-specific placeholders must produce the expected Arrow type.
 */
class ArrowSchemaBuilderTest {

    @Test
    void smallint() {
        ArrowType t = ArrowSchemaBuilder.mapJdbcType(Types.SMALLINT);
        assertThat(t).isInstanceOf(ArrowType.Int.class);
        assertThat(((ArrowType.Int) t).getBitWidth()).isEqualTo(16);
    }

    @Test
    void integer() {
        ArrowType t = ArrowSchemaBuilder.mapJdbcType(Types.INTEGER);
        assertThat(t).isInstanceOf(ArrowType.Int.class);
        assertThat(((ArrowType.Int) t).getBitWidth()).isEqualTo(32);
    }

    @Test
    void bigint() {
        ArrowType t = ArrowSchemaBuilder.mapJdbcType(Types.BIGINT);
        assertThat(t).isInstanceOf(ArrowType.Int.class);
        assertThat(((ArrowType.Int) t).getBitWidth()).isEqualTo(64);
    }

    @Test
    void booleanType() {
        ArrowType t = ArrowSchemaBuilder.mapJdbcType(Types.BOOLEAN);
        assertThat(t).isInstanceOf(ArrowType.Bool.class);
    }

    @Test
    void varcharText() {
        assertThat(ArrowSchemaBuilder.mapJdbcType(Types.VARCHAR))
                .isInstanceOf(ArrowType.Utf8.class);
        assertThat(ArrowSchemaBuilder.mapJdbcType(Types.LONGVARCHAR))
                .isInstanceOf(ArrowType.Utf8.class);
    }

    @Test
    void uuidType() {
        ArrowType t = ArrowSchemaBuilder.mapJdbcType(Types.OTHER, "uuid", 0, 0);
        assertThat(t).isInstanceOf(ArrowType.Utf8.class);
    }

    @Test
    void date() {
        ArrowType t = ArrowSchemaBuilder.mapJdbcType(Types.DATE);
        assertThat(t).isInstanceOf(ArrowType.Date.class);
    }

    @Test
    void timestampNoTz() {
        ArrowType t = ArrowSchemaBuilder.mapJdbcType(Types.TIMESTAMP);
        assertThat(t).isInstanceOf(ArrowType.Timestamp.class);
        assertThat(((ArrowType.Timestamp) t).getTimezone()).isNull();
    }

    @Test
    void timestampTz() {
        ArrowType t = ArrowSchemaBuilder.mapJdbcType(Types.TIMESTAMP_WITH_TIMEZONE);
        assertThat(t).isInstanceOf(ArrowType.Timestamp.class);
        assertThat(((ArrowType.Timestamp) t).getTimezone()).isEqualTo("UTC");
    }

    @Test
    void timestampTzByName() {
        ArrowType t = ArrowSchemaBuilder.mapJdbcType(Types.TIMESTAMP, "timestamptz", 0, 0);
        assertThat(t).isInstanceOf(ArrowType.Timestamp.class);
        assertThat(((ArrowType.Timestamp) t).getTimezone()).isEqualTo("UTC");
    }

    @Test
    void numeric() {
        ArrowType t = ArrowSchemaBuilder.mapJdbcType(Types.NUMERIC, "numeric", 18, 4);
        assertThat(t).isInstanceOf(ArrowType.Decimal.class);
        assertThat(((ArrowType.Decimal) t).getPrecision()).isEqualTo(18);
        assertThat(((ArrowType.Decimal) t).getScale()).isEqualTo(4);
    }

    @Test
    void doublePrecision() {
        ArrowType t = ArrowSchemaBuilder.mapJdbcType(Types.DOUBLE);
        assertThat(t).isInstanceOf(ArrowType.FloatingPoint.class);
        assertThat(((ArrowType.FloatingPoint) t).getPrecision())
                .isEqualTo(org.apache.arrow.vector.types.FloatingPointPrecision.DOUBLE);
    }

    @Test
    void real() {
        ArrowType t = ArrowSchemaBuilder.mapJdbcType(Types.REAL);
        assertThat(t).isInstanceOf(ArrowType.FloatingPoint.class);
        assertThat(((ArrowType.FloatingPoint) t).getPrecision())
                .isEqualTo(org.apache.arrow.vector.types.FloatingPointPrecision.SINGLE);
    }

    @Test
    void bytea() {
        ArrowType t = ArrowSchemaBuilder.mapJdbcType(Types.BINARY, "bytea", 0, 0);
        assertThat(t).isInstanceOf(ArrowType.Binary.class);
    }

    @Test
    void varbinary() {
        ArrowType t = ArrowSchemaBuilder.mapJdbcType(Types.VARBINARY);
        assertThat(t).isInstanceOf(ArrowType.Binary.class);
    }

    @Test
    void jsonb() {
        ArrowType t = ArrowSchemaBuilder.mapJdbcType(Types.OTHER, "jsonb", 0, 0);
        assertThat(t).isInstanceOf(ArrowType.Utf8.class);
    }

    @Test
    void json() {
        ArrowType t = ArrowSchemaBuilder.mapJdbcType(Types.OTHER, "json", 0, 0);
        assertThat(t).isInstanceOf(ArrowType.Utf8.class);
    }

    @Test
    void mysqlBit() {
        ArrowType t = ArrowSchemaBuilder.mapJdbcType(Types.OTHER, "bit", 0, 0);
        assertThat(t).isInstanceOf(ArrowType.Int.class);
    }

    @Test
    void oracleNumber() {
        ArrowType t = ArrowSchemaBuilder.mapJdbcType(Types.OTHER, "number", 10, 2);
        assertThat(t).isInstanceOf(ArrowType.Decimal.class);
        assertThat(((ArrowType.Decimal) t).getPrecision()).isEqualTo(10);
    }

    @Test
    void sqlServerMoney() {
        ArrowType t = ArrowSchemaBuilder.mapJdbcType(Types.OTHER, "money", 0, 0);
        assertThat(t).isInstanceOf(ArrowType.Decimal.class);
        assertThat(((ArrowType.Decimal) t).getScale()).isEqualTo(4);
    }

    @Test
    void unknownDegradesToUtf8() {
        ArrowType t = ArrowSchemaBuilder.mapJdbcType(12345);
        assertThat(t).isInstanceOf(ArrowType.Utf8.class);
    }

    @Test
    void tinyint() {
        ArrowType t = ArrowSchemaBuilder.mapJdbcType(Types.TINYINT);
        assertThat(t).isInstanceOf(ArrowType.Int.class);
        assertThat(((ArrowType.Int) t).getBitWidth()).isEqualTo(8);
    }
}
