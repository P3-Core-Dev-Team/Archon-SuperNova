package com.archon.openmetadata.common.models;
import javax.persistence.Entity;
import javax.persistence.Id;
import javax.persistence.Table;
import lombok.Data;
import lombok.AllArgsConstructor;
import lombok.NoArgsConstructor;

@Entity
@Table(name = "system_properties")
@Data
@NoArgsConstructor
@AllArgsConstructor
public class SystemProperty {
    @Id
    private String propKey;
    private Object propValue;
}
