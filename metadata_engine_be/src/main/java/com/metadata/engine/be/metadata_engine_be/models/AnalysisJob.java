package com.metadata.engine.be.metadata_engine_be.models;

import jakarta.persistence.*;
import lombok.Data;
import lombok.NoArgsConstructor;
import lombok.AllArgsConstructor;
import java.time.Instant;

@Entity
@Table(name = "analysis_job")
@Data
@NoArgsConstructor
@AllArgsConstructor
public class AnalysisJob {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "connection_id")
    private com.metadata.engine.be.metadata_engine_be.models.DataConnection profile;

    @Column(name = "target_schema")
    private String targetSchema;

    @Column(name = "status")
    private String status;

    @Column(name = "start_time")
    private Instant startTime;

    @Column(name = "end_time")
    private Instant endTime;

    @Column(name = "audit_logs", columnDefinition = "TEXT")
    private String auditLogs;
}
