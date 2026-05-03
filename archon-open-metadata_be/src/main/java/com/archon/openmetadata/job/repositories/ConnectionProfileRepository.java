package com.archon.openmetadata.job.repositories;

import com.archon.openmetadata.job.models.ConnectionProfile;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.stereotype.Repository;

@Repository
public interface ConnectionProfileRepository
    extends JpaRepository<ConnectionProfile, UUID>, JpaSpecificationExecutor<ConnectionProfile> {}
