package com.metadata.engine.be.metadata_engine_be.models;

import jakarta.persistence.*;
import lombok.Data;

@Data
@Entity
@Table(name = "sensitive_columns")
public class SensitiveColumn {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "job_id")
    private Long jobId;

    @Transient
    private String transientTableName;

    @com.fasterxml.jackson.annotation.JsonIgnoreProperties({"columns", "hibernateLazyInitializer", "handler"})
    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "table_id")
    private DiscoveredTable table;
    private String columnName;
    private String category;
}
