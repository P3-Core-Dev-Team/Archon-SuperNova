package com.metadata.engine.be.metadata_engine_be.models;

import jakarta.persistence.*;
import lombok.Data;

@Data
@Entity
@Table(name = "data_connections")
public class DataConnection {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    private String url;
    private String username;
    private String password;
    private String schemaName;

    @com.fasterxml.jackson.annotation.JsonIgnore
    @OneToMany(mappedBy = "profile", fetch = FetchType.LAZY)
    private java.util.List<AnalysisJob> jobHistory;
}
