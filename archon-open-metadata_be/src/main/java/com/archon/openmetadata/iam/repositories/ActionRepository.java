package com.archon.openmetadata.iam.repositories;

import com.archon.openmetadata.iam.models.Action;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.stereotype.Repository;

@Repository
public interface ActionRepository
    extends JpaRepository<Action, UUID>, JpaSpecificationExecutor<Action> {}
