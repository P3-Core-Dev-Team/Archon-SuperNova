package com.metadata.engine.be.metadata_engine_be.repositories;

import com.metadata.engine.be.metadata_engine_be.models.DataConnection;
import org.springframework.data.jpa.repository.JpaRepository;

public interface DataConnectionRepository extends JpaRepository<DataConnection, Long> {
}
