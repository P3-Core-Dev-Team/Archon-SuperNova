package com.metadata.engine.be.metadata_engine_be.models;

import jakarta.persistence.*;
import lombok.Data;

@Data
@Entity
@Table(name = "relationships")
public class Relationship {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "job_id")
    private Long jobId;

    @Transient
    private String sourceTableName;

    @com.fasterxml.jackson.annotation.JsonIgnoreProperties({"columns", "hibernateLazyInitializer", "handler"})
    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "source_table_id")
    private DiscoveredTable sourceTable;
    
    @Column(name = "source_column")
    private String sourceColumn;

    @Transient
    private String targetTableName;

    @com.fasterxml.jackson.annotation.JsonIgnoreProperties({"columns", "hibernateLazyInitializer", "handler"})
    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "target_table_id")
    private DiscoveredTable targetTable;
    
    @Column(name = "target_column")
    private String targetColumn;
    
    private Double score;
    private String cardinality;
}
