package com.archon.openmetadata.iam.models;

import com.fasterxml.jackson.annotation.JsonIgnore;
import java.util.UUID;
import javax.persistence.*;
import lombok.Data;
import org.hibernate.annotations.GenericGenerator;

@Entity
@Table(name = "user_preferences")
@Data
public class UserPreference {

    @Id
    @GeneratedValue(generator = "UUID")
    @GenericGenerator(name = "UUID", strategy = "org.hibernate.id.UUIDGenerator")
    private UUID id;

    @OneToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "user_id", referencedColumnName = "id")
    @JsonIgnore
    private User user;

    private String theme = "System default";
    
    @Column(name = "date_format")
    private String dateFormat = "YYYY-MM-DD";
    
    private String timezone = "America/New_York";
    
    @Column(name = "default_graph_view")
    private String defaultGraphView = "Domain level";
}
